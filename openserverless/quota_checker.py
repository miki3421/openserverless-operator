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
import logging, json,  os
import openserverless.kube as kube

from openserverless.postgres_client import PostgresClient
from openserverless.redis_client import RedisClient

FERRRET_DB_QUOTA_ANNOTATION = "ferret_db_quota_reached"
POSTGRES_DB_QUOTA_ANNOTATION = "postgres_db_quota_reached"
REDIS_DB_QUOTA_ANNOTATION = "redis_db_quota_reached"

def check_mongo_quota(client, users):
    from openserverless.mongodb_client import tenant_names
    for wsku in users:
        spec=wsku["spec"]
        mongo=spec.get("mongodb",{})
        if not mongo.get("enabled") or mongo.get("quota") is None:
            continue
        limit=None if mongo["quota"] == "auto" else int(mongo["quota"])*1024*1024
        if limit is not None and limit<=0:
            raise ValueError("MongoDB quota must be a positive number of MiB")
        user,database=tenant_names(spec["namespace"],mongo["database"])
        reached=limit is not None and client.usage(database)>=limit
        # Reconcile roles as well as the annotation after any interrupted run.
        client.set_readonly(user,database,reached)
        annotate(wsku["metadata"]["name"],f"{FERRRET_DB_QUOTA_ANNOTATION}={str(reached).lower()}")

def annotate(wsku_name, keyval):
    """
    Annotate in overwrite mode a wksu resource with the specified key=value input param.
    Used to mark the given wsku as already processed.
    param: wksu_name the name of a wksu object too be annotated
    param: keyval a key value pair specifying the annotation to be set
    """
    kube.kubectl("annotate", f"wsku/{wsku_name}",  keyval, "--overwrite")

def get_wsk_users(jsonpath,namespace="openserverless"):
    """
    Queries for openserverless wsku users matching the given jsonpath
    param: jsonpath
    param: namespace (defaults to openserverless)
    return: an array of mathching record or emtpy [] if none is found
    """
    logging.info(f"querying wsku entries matching {jsonpath}")
    users = kube.kubectl("get", "wsku", namespace=namespace, jsonpath=jsonpath,debugresult=False)
    if(users):
        return users


    logging.warning(f"querying wsku with {jsonpath} returned empty response")
    return []

def block_pg_user_quota(pg_client:PostgresClient,wsku_name, pg_db_name, quota_annotation):
    """
    Revoke user access from a Postgres database and annotate it with a quota reached annotation
    """
    if pg_client.revoke_access_from_db(pg_db_name, pg_db_name):
        annotate(wsku_name,f"{quota_annotation}=true")

def reset_pg_user_quota(pg_client:PostgresClient,wsku_name, pg_db_name, quota_annotation):
    """
    RE-Grant user access from a Postgres database and annotate it with a quota reached annotation
    """
    if pg_client.grant_access_on_db(pg_db_name, pg_db_name):
        annotate(wsku_name,f"{quota_annotation}=false")        

def check_pg_quota(pg_client:PostgresClient, pg_dbsize_list, pg_wsku, check_ferretdb=False):
    """
    Check PG quota as both Postgres and FerretDB databases.
    """
    logging.info("***** Checking Postgres database limit ****") 

    if len(pg_wsku) == 0:
        logging.info("no openserverless wsku resource with PG based database quota limit found!")
        return

    for wsku in pg_wsku:
        spec = wsku['spec']
        metadata = wsku['metadata']
        wsku_name = metadata['name']
        quota_annotation = check_ferretdb and FERRRET_DB_QUOTA_ANNOTATION or POSTGRES_DB_QUOTA_ANNOTATION
        quota_applied = "false"

        pg_db= check_ferretdb and f"{spec['mongodb']['database']}_ferretdb" or spec['postgres']['database']
        pg_db_quota = check_ferretdb and int(spec['mongodb']['quota'])*1014*1024 or int(spec['postgres']['quota'])*1024*1024

        # Check if the quota annotations has been already applied
        if "annotations" in metadata and quota_annotation in metadata["annotations"]:
            quota_applied = metadata["annotations"][quota_annotation]

        logging.info(f"PG database {pg_db} enforced quota is set to {pg_db_quota} bytes")
        if pg_db in pg_dbsize_list:
            current_pg_db_quota = pg_dbsize_list[pg_db]
            
            if current_pg_db_quota >= pg_db_quota:
                if quota_applied in ["false"]:
                    block_pg_user_quota(pg_client, 
                                wsku_name, 
                                pg_db,
                                quota_annotation)
                    continue    
                else:
                    logging.info(f"***** Postgres DB {pg_db} size {current_pg_db_quota} is exceeding quota limit {pg_db_quota}, but revoke has been already executed ****")
                    continue
            
            if current_pg_db_quota < pg_db_quota and quota_applied in ["true"]:
                reset_pg_user_quota(pg_client, 
                              wsku_name, 
                              pg_db,
                              quota_annotation)
                continue 

            logging.info(f"***** Postgres DB {pg_db} size {current_pg_db_quota} is not exceeding quota limit {pg_db_quota} ****")    
        else:
            logging.warning(f"**** PG database {pg_db} missing from Postgres DB allocated size")

def block_redis_prefix_quota(redis_client: RedisClient, wsku_name, namespace, prefix, quota_annotation):
    """
    SET the given redis/valkey with @READ permission on the given prefix from the specified REDIS/VALKEY AUTH.
    """
    res = redis_client.set_prefix_readonly(namespace, prefix)
    if res:
        logging.info(res)
        annotate(wsku_name,f"{quota_annotation}=true")

def reset_redis_prefix_quota(redis_client: RedisClient, wsku_name, namespace, prefix, quota_annotation):
    """
    SET the given redis/valkey with @ALL permission on the given prefix from the specified REDIS/VALKEY AUTH. 
    """
    res = redis_client.set_prefix_all(namespace, prefix)
    if res:
        annotate(wsku_name,f"{quota_annotation}=false")              

def check_redis_quota(redis_client: RedisClient, redis_wsku):
    """
    Check REDIS/VALKEY quota
    """
    logging.info("***** Checking Redis Cache limit ****")

    if len(redis_wsku) == 0:
        logging.info("no openserverless wsku resource with enforced REDIS quota limit found!")
        return

    for wsku in redis_wsku:        
        spec = wsku['spec']
        metadata = wsku['metadata']
        wsku_name = metadata['name']
        prefix = f"{spec['redis']['prefix']}"
        namespace = spec['namespace']
        quota_applied = "false"
        redis_quota = int(spec['redis']['quota'])*1014*1024

        if(not prefix.endswith(":")):
            prefix = f"{prefix}:"

        # Check if the quota annotations has been already applied
        if "annotations" in metadata and REDIS_DB_QUOTA_ANNOTATION in metadata["annotations"]:
            quota_applied = metadata["annotations"][REDIS_DB_QUOTA_ANNOTATION]

        logging.info(f"REDIS prefix {prefix} enforced quota is set to {redis_quota} bytes")        
        current_redis_db_quota = redis_client.calculate_prefix_allocated_size(prefix)
            
        if current_redis_db_quota >= redis_quota:
                if quota_applied in ["false"]:
                    block_redis_prefix_quota(redis_client,
                                wsku_name ,
                                namespace, 
                                prefix,
                                REDIS_DB_QUOTA_ANNOTATION)
                    continue
                else:
                    logging.info(f"***** REDIS prefix {prefix} size {current_redis_db_quota} is exceeding quota limit {redis_quota}, but revoke has been already executed ****")
                    continue 
            
        if current_redis_db_quota < redis_quota and quota_applied in ["true"]:
                reset_redis_prefix_quota(redis_client,
                                wsku_name ,
                              namespace, 
                              prefix,
                              REDIS_DB_QUOTA_ANNOTATION)
                continue

        logging.info(f"***** REDIS prefix {prefix} size {current_redis_db_quota} is not exceeding quota limit {redis_quota} ****")   

def start():
    """
    Queries for wsku users resources having postgres db quota enables and 
    verifies that the corresponding database space allocations does not exceed the
    quota size specific in megabytes.

    If the quota is exceeded, user will be set in readonly mode and no more write TX will be accepted. 
    """
    logging.basicConfig(level=logging.INFO)
    logging.info("****** OPENSERVERLESS Quota enforcer started *****")

    pg_wsku = get_wsk_users("{.items[?(@.spec.postgres.quota != 'auto')]}")
    pg_wsku=[u for u in pg_wsku if u["spec"].get("postgres",{}).get("enabled") and u["spec"]["postgres"].get("quota") not in (None,"auto")]
    if pg_wsku and os.environ.get("POSTGRES_ENABLED","true")!="false":
        pg_client=PostgresClient(os.environ.get("DATABASE_DB_HOST_NAME"),5432,os.environ.get("PG_USER"),os.environ.get("PG_PASSWORD"))
        check_pg_quota(pg_client,pg_client.query_all_pg_database_size(),pg_wsku)
    if os.environ.get("MONGODB_ENABLED")=="true":
        from openserverless.mongodb_native import admin_client
        mongo_wsku=get_wsk_users("{.items[?(@.spec.mongodb.enabled == true)]}")
        if any(u["spec"].get("mongodb",{}).get("enabled") and u["spec"]["mongodb"].get("quota") is not None for u in mongo_wsku):
            client=admin_client()
            try:
                check_mongo_quota(client,mongo_wsku)
            finally:
                client.close()
    redis_wsku = get_wsk_users("{.items[?(@.spec.redis.quota != 'auto')]}")
    redis_wsku=[u for u in redis_wsku if u["spec"].get("redis",{}).get("enabled") and u["spec"]["redis"].get("quota") not in (None,"auto")]
    if redis_wsku and os.environ.get("REDIS_ENABLED","true")!="false":
        check_redis_quota(RedisClient(os.environ.get("REDIS_PASSWORD")),redis_wsku)

    logging.info("****** OPENSERVERLESS Quota enforcer ended *****")

