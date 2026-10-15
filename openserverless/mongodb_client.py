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
# this module wraps kubectl
import json
import hashlib
from pathlib import Path

"""MongoDB tenant lifecycle. No tenant receives a role on another database."""
import re
import time
from urllib.parse import quote
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

def tenant_names(namespace, database):
    for value in [namespace, database]:
        if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]+",value):
            raise ValueError("Invalid MongoDB tenant identifier")
    # Keep the configured logical database name (normally the OPS login).
    result = database
    if result in {"admin","local","config","openserverless"}:
        raise ValueError("MongoDB tenant database name is reserved for system use")
    if len(result.encode()) > 63:
        raise ValueError("MongoDB tenant database name exceeds 63 bytes")
    # Preserve the public OPS tenant identity when replacing FerretDB.
    # Applications may validate this username before opening a connection.
    return namespace + "_ferretdb", result

def tenant_uri(host, user, password, database, replica_set="ops-mongodb"):
    return f"mongodb://{quote(user,safe='')}:{quote(password,safe='')}@{host}:27017/{quote(database,safe='')}?authSource={quote(database,safe='')}&authMechanism=SCRAM-SHA-256&replicaSet={quote(replica_set,safe='')}&retryWrites=true"

class NativeMongo:
    def __init__(self, host, user, password, replica_set="ops-mongodb"):
        self.host=host
        self.replica_set=replica_set
        self.client=MongoClient(host,27017,username=user,password=password,authSource="admin",authMechanism="SCRAM-SHA-256",directConnection=True,
                                serverSelectionTimeoutMS=5000,connectTimeoutMS=5000,socketTimeoutMS=20000)
    def close(self):
        self.client.close()
    def initialize(self, member, timeout=300):
        deadline=time.monotonic()+timeout
        initiated=False
        while time.monotonic()<deadline:
            try:
                hello=self.client.admin.command("hello")
                if hello.get("setName") not in (None,self.replica_set):
                    raise RuntimeError("MongoDB belongs to a different replica set")
                if hello.get("isWritablePrimary") and hello.get("setName")==self.replica_set:
                    return True
                if not initiated:
                    try:
                        self.client.admin.command("replSetGetStatus")
                    except OperationFailure as exc:
                        if exc.code!=94:
                            raise
                        self.client.admin.command("replSetInitiate",{"_id":self.replica_set,"members":[{"_id":0,"host":member+":27017"}]})
                    initiated=True
            except PyMongoError:
                pass  # Startup/auth/election may still be in progress; no credential logs.
            time.sleep(2)
        raise RuntimeError("Native MongoDB initialization did not complete within its timeout")
    def provision(self, user, password, database):
        if not isinstance(password,str) or not password:
            raise ValueError("MongoDB tenant password must be set")
        db=self.client[database]
        existing=db.command("usersInfo",user)["users"]
        # A configuration retry must not clear an already-enforced read-only quota.
        role="read" if existing and existing[0].get("roles")==[{"role":"read","db":database}] else "readWrite"
        options={"pwd":password,"roles":[{"role":role,"db":database}],"mechanisms":["SCRAM-SHA-256"]}
        db.command("updateUser" if existing else "createUser",user,**options)
        return True
    def remove(self, user, database):
        db=self.client[database]
        users=db.command("usersInfo",1)["users"]
        if any(u["user"]==user for u in users):
            db.command("dropUser",user)
        # Keep shared databases if another explicitly provisioned owner remains.
        if not any(u["user"]!=user for u in users):
            self.client.drop_database(database)
        return True
    def usage(self,database):
        stats=self.client[database].command("dbStats",scale=1)
        return int(stats.get("storageSize",0))+int(stats.get("indexSize",0))
    def set_readonly(self,user,database,readonly):
        self.client[database].command("updateUser",user,roles=[{"role":"read" if readonly else "readWrite","db":database}])
        return True
