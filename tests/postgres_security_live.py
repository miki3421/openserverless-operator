# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
"""Explicit PostgreSQL live checks. Creates and cleans only uniquely named lab DBs."""
import json
import logging
import secrets
import sys
from unittest.mock import patch

from openserverless import config as cfg, kube, util, postgres_operator as operator, quota_checker
from openserverless.postgres_client import PostgresClient

logging.disable(logging.CRITICAL)
cfg.configure(kube.get('wsk/controller')['spec'])
cfg.detect()
data = util.get_postgres_config_data()
service = util.get_service_by_selector('app=openserverless-postgres', '{.items[?(@.metadata.labels.replicationRole == "primary")]}')
host = service['metadata']['name'] + '.openserverless.svc.cluster.local'
admin = PostgresClient(host, 5432, 'postgres', data['postgres_root_password'])
mode = sys.argv[1]
checks = []
resources = []

def record(name, success=True):
    if not success:
        raise AssertionError(name)
    checks.append({'name': name, 'status': 'PASS'})

def sql(conn, statement, args=None):
    cur = conn.cursor()
    try:
        cur.execute(statement, args) if args is not None else cur.execute(statement)
        try:return cur.fetchall()
        except Exception:return []
    finally:cur.close()

root = admin.get_connection_to_db('postgres');root.autocommit=True

def new_tenant(suffix=''):
    token = secrets.token_hex(5)
    role = 'pgsecr' + token + suffix
    db = 'pgsecd' + token + suffix
    password = secrets.token_urlsafe(24)
    sql(root, f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
    resources.append((role, db))
    sql(root, f'CREATE DATABASE "{db}"')
    sql(root, f'REVOKE CONNECT ON DATABASE "{db}" FROM PUBLIC')
    sql(root, f'GRANT ALL PRIVILEGES ON DATABASE "{db}" TO "{role}"')
    control = admin.get_connection_to_db(db);control.autocommit=True
    sql(control, f'CREATE SCHEMA "{role}_schema" AUTHORIZATION "{role}"')
    sql(control, f'ALTER DATABASE "{db}" SET search_path TO "{role}_schema", pg_catalog')
    control.close()
    client = PostgresClient(host, 5432, role, password)
    conn = client.get_connection_to_db(db);conn.autocommit=True
    sql(conn, 'CREATE TABLE owned_data(id int PRIMARY KEY, value text)')
    sql(conn, "INSERT INTO owned_data VALUES(1, 'synthetic')")
    return role, db, client, conn

try:
    if mode == 'before':
        role, db, client, conn = new_tenant()
        record('old_quota_reports_success', admin.revoke_access_from_db(role, db))
        sql(conn, 'GRANT ALL ON owned_data TO CURRENT_USER')
        sql(conn, f'GRANT ALL ON SCHEMA "{role}_schema" TO CURRENT_USER')
        sql(conn, "INSERT INTO owned_data VALUES(2, 'owner-still-writes')")
        sql(conn, 'CREATE TABLE owner_still_creates(id int)')
        record('old_quota_bypass_reproduced')
        conn.close()
    else:
        # Exercise generation and persistence against a real, isolated Secret.
        # Only resource lookups are scoped away from the installed database.
        from openserverless import postgres_credentials as credentials
        name='pgsec-credentials-'+secrets.token_hex(5)
        real_run=credentials._run
        real_get=cfg.get
        def isolated_run(*args, **kwargs):
            args=list(args)
            if len(args)>2 and args[:2]==['get','kubegres']:
                args[2]=name
            if args[:2]==['get','pvc']:
                args=[value.replace('app=openserverless-postgres','app='+name) for value in args]
            return real_run(*args,**kwargs)
        def unconfigured(key,*args,**kwargs):
            return None if key in ('postgres.admin.password','postgres.openserverless.password') else real_get(key,*args,**kwargs)
        try:
            with patch.object(credentials,'SECRET_NAME',name), patch.object(credentials,'_run',side_effect=isolated_run), patch.object(cfg,'get',side_effect=unconfigured):
                generated=credentials.credentials(create=True)
                first=kube.get('secret/'+name)['metadata']['resourceVersion']
                record('fresh_credentials_unique',len(set(generated.values()))==3)
                record('generated_credentials_persist',generated==credentials.credentials(create=True))
                record('reconcile_does_not_rewrite_secret',first==kube.get('secret/'+name)['metadata']['resourceVersion'])
        finally:
            kube.kubectl('delete','secret',name,'--ignore-not-found',debugresult=False)
        # Existing installation secrets are preserved through ordinary reconciliation.
        record('credentials_remain_stable', data == util.get_postgres_config_data(create=True))
        for legacy in ('0therPa55', '0therPa55sd'):
            try:
                connection=PostgresClient(host,5432,'postgres',legacy).get_connection_to_db('postgres')
            except Exception:
                record('legacy_admin_password_rejected_'+legacy[-2:])
            else:
                connection.close();raise AssertionError('Legacy admin login succeeded')
        victim, victimdb, victimclient, victimconn = new_tenant()
        victimconn.close()
        for ferret in (False, True):
            role, db, client, conn = new_tenant('_ferretdb' if ferret else '')
            namespace=role.removesuffix('_ferretdb') if ferret else role
            database=db.removesuffix('_ferretdb') if ferret else db
            component='mongodb' if ferret else 'postgres'
            annotation=quota_checker.FERRRET_DB_QUOTA_ANNOTATION if ferret else quota_checker.POSTGRES_DB_QUOTA_ANNOTATION
            user={'metadata':{'name':'synthetic-wsku'},'spec':{'namespace':namespace,component:{'database':database,'quota':1}}}
            sizes=admin.query_all_pg_database_size()
            with patch.object(quota_checker,'annotate') as annotate:
                quota_checker.check_pg_quota(admin,sizes,[user],ferret)
                annotate.assert_called_once_with('synthetic-wsku',annotation+'=true')
            record('quota_exact_role_suspended_'+component, sql(root,'SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',(role,))[0][0] is False)
            try:sql(conn,"INSERT INTO owned_data VALUES(2,'must-fail')")
            except Exception:record('active_owner_session_terminated_'+component)
            else:raise AssertionError('Owner session can still write')
            try:conn.close()
            except Exception:pass  # pg8000 may report the already terminated socket.
            try:connection=client.get_connection_to_db(db)
            except Exception:record('new_owner_login_denied_'+component)
            else:connection.close();raise AssertionError('Suspended owner login succeeded')
            untouched=victimclient.get_connection_to_db(victimdb);untouched.close()
            record('other_tenant_unaffected_'+component)
            # A legacy or stale true annotation must not bypass the new barrier.
            sql(root,f'ALTER ROLE "{role}" LOGIN')
            user['metadata']['annotations']={annotation:'true'}
            with patch.object(quota_checker,'annotate'):
                quota_checker.check_pg_quota(admin,sizes,[user],ferret)
            record('legacy_annotation_reenforced_'+component,sql(root,'SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',(role,))[0][0] is False)
            user['spec'][component]['quota']=1024
            with patch.object(quota_checker,'annotate') as annotate:
                quota_checker.check_pg_quota(admin,sizes,[user],ferret)
                annotate.assert_called_once_with('synthetic-wsku',annotation+'=false')
            conn=client.get_connection_to_db(db);conn.autocommit=True
            record('existing_data_preserved_'+component,sql(conn,'SELECT value FROM owned_data WHERE id=1')[0][0]=='synthetic')
            sql(conn,"INSERT INTO owned_data VALUES(3,'restored')")
            sql(conn,'CREATE TABLE ddl_restored(id int)')
            record('owner_dml_and_ddl_restored_'+component)
            conn.close()
        # Use the actual provisioning renderer and psql path, including weird
        # passwords that used to cross SQL and psql parser boundaries.
        token=secrets.token_hex(5);role='pgsecp'+token;db='pgsecb'+token
        resources.append((role,db))
        values={'namespace':role,'postgres.database':db,'postgres.password':None}
        class Config:
            def get(self,key):return values[key]
        class Metadata:
            def add_metadata(self,*args):pass
        for password in ["x'; ALTER ROLE " + victim + " SUPERUSER; --", "quotes'\\and$$日本語"]:
            values['postgres.password']=password
            record('provisioning_accepts_literal_password',bool(operator.create_db_user(Config(),Metadata())))
            connection=PostgresClient(host,5432,role,password).get_connection_to_db(db)
            connection.autocommit=True
            record('password_authenticates_exactly',sql(connection,'SELECT current_user')[0][0]==role)
            connection.close()
            record('injection_cannot_change_other_role',sql(root,'SELECT rolsuper FROM pg_roles WHERE rolname=%s',(victim,))[0][0] is False)
        record('provisioned_tenant_delete',bool(operator.delete_db_user(role,db)))
        resources.remove((role,db))
    print(json.dumps({'mode':mode,'checks':checks,'count':len(checks)}))
finally:
    for role,db in reversed(resources):
        sql(root,'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(db,))
        sql(root,f'DROP DATABASE IF EXISTS "{db}"')
        sql(root,f'DROP ROLE IF EXISTS "{role}"')
    root.close()
