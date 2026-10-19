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
import base64
import json
import logging
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from openserverless import config, postgres_credentials as credentials
from openserverless import postgres_operator as operator, quota_checker
from openserverless.postgres_client import PostgresClient
from openserverless.user_metadata import UserMetadata


class CredentialTests(unittest.TestCase):
    def setUp(self):
        config.clean()

    @staticmethod
    def secret(value="unique-installation-password"):
        return json.dumps({"data": {k: base64.b64encode((value + ("-" + k if value not in credentials.LEGACY_PASSWORDS else "")).encode()).decode()
                                    for k in credentials.FIELDS}})

    def test_existing_credentials_are_stable(self):
        with patch.object(credentials, "_run", return_value=self.secret()) as run:
            a = credentials.credentials()
            self.assertEqual(a, credentials.credentials(create=True))
            self.assertTrue(all(c.args[0] == "get" for c in run.call_args_list))

    def test_fresh_installations_have_unique_independent_credentials(self):
        captured = []
        def run(*args, **kwargs):
            if args[0] == "create":
                captured.append(json.loads(kwargs["input"]))
            return ""
        with patch.object(credentials, "_run", side_effect=run):
            a = credentials.credentials(create=True)
            b = credentials.credentials(create=True)
        self.assertEqual(len(set(a.values()) | set(b.values())), 6)
        self.assertEqual(len(captured), 2)

    def test_explicit_passwords_are_preserved(self):
        config.configure({"postgres": {"admin": {"password": "custom:admin\\pass"},
                                      "openserverless": {"password": "custom-app-password"}}})
        with patch.object(credentials, "_run", return_value=""):
            values = credentials.credentials(create=True)
        self.assertEqual(values["postgres_root_password"], "custom:admin\\pass")
        self.assertEqual(values["postgres_openserverless_password"], "custom-app-password")
        self.assertNotEqual(values["postgres_root_password"], values["postgres_root_replica_password"])

    def test_legacy_values_are_rejected_from_secret_and_configuration(self):
        for value in credentials.LEGACY_PASSWORDS:
            with self.subTest(value=value), patch.object(credentials, "_run", return_value=self.secret(value)):
                with self.assertRaises(ValueError):
                    credentials.credentials()
            config.configure({"postgres": {"admin": {"password": value}}})
            with patch.object(credentials, "_run", return_value="") as run:
                with self.assertRaises(ValueError):
                    credentials.credentials(create=True)
                self.assertFalse(any(c.args[0] == "create" for c in run.call_args_list))
        config.clean()

    def test_missing_secret_never_invents_passwords_for_live_resources(self):
        with patch.object(credentials, "_run", side_effect=["", '{"kind":"Kubegres"}']):
            with self.assertRaises(RuntimeError):
                credentials.credentials(create=True)
        with patch.object(credentials, "_run", side_effect=["", "", '{"items":[{"kind":"PVC"}]}']):
            with self.assertRaises(RuntimeError):
                credentials.credentials(create=True)
        with patch.object(credentials, "_run", return_value=""):
            with self.assertRaises(RuntimeError):
                credentials.credentials()

    def test_mismatch_and_corruption_fail_closed(self):
        config.configure({"postgres": {"admin": {"password": "new-unrotated-password"}}})
        with patch.object(credentials, "_run", return_value=self.secret()):
            with self.assertRaises(ValueError):
                credentials.credentials()
        config.clean()
        with patch.object(credentials, "_run", return_value='{"data":{}}'):
            with self.assertRaises(KeyError):
                credentials.credentials()

    def test_create_preserves_kubectl_output_contract(self):
        data = {"postgres_root_password": "root-password", "postgres_root_replica_password": "replica-password",
                "postgres_openserverless_password": "platform-password", "backup": False}
        with patch.object(operator.util, "get_postgres_config_data", return_value=data) as get_data, \
                patch.object(operator.util, "postgres_manager_affinity_tolerations_data", return_value={}), \
                patch.object(operator.util, "wait_for_pod_ready"), \
                patch.object(operator.util, "wait_for_service"), \
                patch.object(operator.kus, "patchTemplates", return_value=""), \
                patch.object(operator.kus, "patchGenericEntry", return_value=""), \
                patch.object(operator.kus, "kustom_list", return_value={"items": []}), \
                patch.object(operator.kube, "apply", return_value="operator applied\n"), \
                patch.object(credentials, "_run", return_value="database applied\n"), \
                patch.object(operator, "update_system_cm_for_pdb"):
            result = operator.create()
        get_data.assert_called_once_with(create=True, owner=None)
        self.assertEqual(result, "operator applied\ndatabase applied\n")

    def test_private_failure_does_not_expose_input_or_stderr(self):
        result = subprocess.CompletedProcess([], 1, "", "SENSITIVE")
        with patch.object(credentials.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "^Could not access PostgreSQL credentials$"):
                credentials.apply_resources({"stringData": {"secret": "SENSITIVE"}})


class QuotaTests(unittest.TestCase):
    def client(self):
        client = PostgresClient("host", 5432, "postgres", "secret")
        connection = MagicMock()
        connection.cursor.return_value.fetchall.return_value = [(True,)]
        connection.cursor.return_value.fetchone.return_value = (0,)
        client.get_connection_to_db = MagicMock(return_value=connection)
        return client, connection

    def test_owner_suspension_commits_before_termination_and_restores_without_acl_changes(self):
        client, conn = self.client()
        self.assertTrue(client.revoke_access_from_db('tenant-with-dash', 'different_database'))
        sql = [c.args[0] for c in conn.cursor.return_value.execute.call_args_list]
        self.assertIn('ALTER ROLE "tenant-with-dash" NOLOGIN', sql[0])
        self.assertIn('pg_terminate_backend', sql[1])
        self.assertEqual(conn.cursor.return_value.execute.call_args_list[1].args[1], ('tenant-with-dash',))
        calls = conn.mock_calls
        barrier = next(i for i, c in enumerate(calls) if c == unittest.mock.call.commit())
        kill = next(i for i, c in enumerate(calls) if 'pg_terminate_backend' in str(c))
        self.assertLess(barrier, kill)
        self.assertTrue(client.grant_access_on_db('tenant-with-dash', 'different_database'))
        self.assertEqual(conn.cursor.return_value.execute.call_args.args[0], 'ALTER ROLE "tenant-with-dash" LOGIN')
        self.assertFalse(any('GRANT ALL' in s for s in sql))

    def test_session_termination_failure_does_not_claim_success(self):
        client, conn = self.client()
        conn.cursor.return_value.fetchall.return_value = [(False,)]
        self.assertFalse(client.revoke_access_from_db('tenant', 'db'))
        conn.cursor.return_value.fetchall.return_value = []
        conn.cursor.return_value.fetchone.return_value = (1,)
        self.assertFalse(client.revoke_access_from_db('tenant', 'db'))

    def test_role_is_distinct_from_database_in_both_backends(self):
        for ferret in (False, True):
            client = MagicMock()
            db = 'customdb_ferretdb' if ferret else 'customdb'
            role = 'namespace_ferretdb' if ferret else 'namespace'
            annotation = quota_checker.FERRRET_DB_QUOTA_ANNOTATION if ferret else quota_checker.POSTGRES_DB_QUOTA_ANNOTATION
            user = {"spec": {"namespace": "namespace", "postgres": {"database": "customdb", "quota": 1},
                             "mongodb": {"database": "customdb", "quota": 1}}, "metadata": {"name": "wsku-resource"}}
            with patch.object(quota_checker, "annotate") as annotate:
                quota_checker.check_pg_quota(client, {db: 2097152}, [user], ferret)
                client.revoke_access_from_db.assert_called_once_with(role, db)
                annotate.assert_called_once_with('wsku-resource', annotation + '=true')
                user['metadata']['annotations'] = {annotation: 'true'}
                quota_checker.check_pg_quota(client, {db: 0}, [user], ferret)
                client.grant_access_on_db.assert_called_once_with(role, db)

    def test_removing_quota_restores_suspended_role(self):
        client = MagicMock()
        user = {"spec": {"namespace": "tenant", "postgres": {"database": "different_db", "quota": "auto"}},
                "metadata": {"name": "user", "annotations": {quota_checker.POSTGRES_DB_QUOTA_ANNOTATION: "true"}}}
        with patch.object(quota_checker, "annotate"):
            quota_checker.check_pg_quota(client, {}, [user])
        client.grant_access_on_db.assert_called_once_with('tenant', 'different_db')
        client.revoke_access_from_db.assert_not_called()

    def test_failed_revocation_does_not_mark_quota_enforced(self):
        client = MagicMock()
        client.revoke_access_from_db.return_value = False
        with patch.object(quota_checker, "annotate") as annotate:
            quota_checker.block_pg_user_quota(client, 'wsku', 'role', 'db', 'annotation')
            annotate.assert_not_called()


class LocalOnlyTests(unittest.TestCase):
    def test_provisioning_password_never_enters_sql_as_code(self):
        for password in ["x'; ALTER ROLE other SUPERUSER; --", "x$$;\n\\! id\n--", "quote'\\colon:日本語"]:
            data = {"username": "tenant", "database": "custom_db", "password": password, "mode": "create"}
            path = operator.render_postgres_script('tenant', 'postgres_manage_user_tpl.sql', data)
            try:
                rendered = Path(path).read_text()
                self.assertNotIn(password, rendered)
                self.assertIn(base64.b64encode(password.encode()).decode(), rendered)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            finally:
                os.unlink(path)

    def test_equivalent_ferretdb_entry_and_unsafe_identifiers(self):
        path = operator.render_postgres_script('tenant_ferretdb', 'postgres_manage_user_tpl.sql',
                                              {"username": "tenant_ferretdb", "database": "custom_ferretdb", "password": "ok", "mode": "create"})
        os.unlink(path)
        for field in ('username', 'database'):
            with self.assertRaises(ValueError):
                operator.render_postgres_script('tenant', 'postgres_manage_user_tpl.sql', {field: "x'; SELECT 1;--"})

    def test_metadata_logs_only_keys_and_preserves_profile_values(self):
        metadata = UserMetadata.__new__(UserMetadata)
        metadata._data = {"password": "SENSITIVE_HASH", "metadata": []}
        with self.assertLogs(level=logging.DEBUG) as logs:
            metadata.add_metadata('POSTGRES_PASSWORD', 'SENSITIVE_PASSWORD')
            metadata.add_metadata('POSTGRES_URL', 'postgres://user:SENSITIVE_URL@host/db')
            metadata.dump()
        self.assertNotIn('SENSITIVE', '\n'.join(logs.output))
        self.assertEqual(metadata.get_metadata()['metadata'][0]['value'], 'SENSITIVE_PASSWORD')


if __name__ == '__main__':
    unittest.main()
