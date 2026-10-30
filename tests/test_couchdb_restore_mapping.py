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

"""Initialization accepts restored state only after positive verification."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

class CouchdbRestoreMappingTests(unittest.TestCase):
    def setUp(self):
        source=(Path(__file__).resolve().parents[1]/'openserverless/couchdb_util.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='CouchDB')
        self.requests=SimpleNamespace(get=Mock())
        scope={'req':self.requests};exec(compile(ast.Module(body=[node],type_ignores=[]),'couchdb_util.py','exec'),scope)
        self.db=scope['CouchDB'].__new__(scope['CouchDB'])
        self.db.db_base='http://couchdb:5984/openserverless_';self.db.db_url='http://couchdb:5984';self.db.db_session=Mock()
    def test_existing_database_requires_successful_authenticated_head(self):
        self.db.db_session.put.return_value.status_code=412
        self.db.db_session.head.return_value.status_code=200
        self.assertTrue(self.db.create_db('subjects'))
        self.db.db_session.head.return_value.status_code=403
        self.assertFalse(self.db.create_db('subjects'))
    def test_existing_user_requires_matching_authenticated_identity(self):
        self.db.db_session.put.return_value.status_code=409
        reply=self.requests.get.return_value;reply.status_code=200;reply.json.return_value={'userCtx':{'name':'controller'}}
        self.assertTrue(self.db.add_user('controller','synthetic-password'))
        self.requests.get.assert_called_with('http://couchdb:5984/_session',auth=('controller','synthetic-password'),timeout=10)
        reply.json.return_value={'userCtx':{'name':None}}
        self.assertFalse(self.db.add_user('controller','synthetic-password'))
        reply.status_code=401
        self.assertFalse(self.db.add_user('controller','synthetic-password'))
    def test_unrelated_create_failure_is_not_treated_as_existing_state(self):
        self.db.db_session.put.return_value.status_code=500
        self.assertFalse(self.db.create_db('subjects'))
        self.assertFalse(self.db.add_user('controller','synthetic-password'))
        self.requests.get.assert_not_called()

if __name__=='__main__':unittest.main()
