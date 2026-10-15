# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
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
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
from unittest.mock import Mock, patch
from openserverless import ferretdb


class FerretProvisioningTests(unittest.TestCase):
    def test_create_and_delete_use_valid_namespace_and_primary_with_replication(self):
        user = Mock()
        user.get.side_effect = {"namespace": "opsvalalpha", "mongodb.database": "opsvalalpha",
                                "mongodb.password": "synthetic"}.get
        with patch.object(ferretdb.util, "get_postgres_config_data", return_value={}), \
             patch.object(ferretdb.postgres.ntp, "spool_template", return_value="/tmp/synthetic.sql") as render, \
             patch.object(ferretdb.util, "get_pod_name_by_selector", return_value="primary-0") as select, \
             patch.object(ferretdb.postgres, "exec_psql_command", return_value="OK") as execute, \
             patch.object(ferretdb, "_add_mdb_user_metadata"):
            self.assertEqual(ferretdb.create_db_user(user, Mock()), "OK")
            self.assertEqual(ferretdb.delete_db_user("opsvalalpha", "opsvalalpha"), "OK")
        self.assertEqual(select.call_count, 2)
        for invocation in select.call_args_list:
            self.assertEqual(invocation.args[0], "app=openserverless-postgres")
            self.assertIn("replicationRole == 'primary'", invocation.args[1])
        for invocation in render.call_args_list:
            self.assertIn("__opsvalalpha_", invocation.args[1])
            self.assertEqual(invocation.args[2]["database"], "opsvalalpha_ferretdb")
            self.assertEqual(invocation.args[2]["username"], "opsvalalpha_ferretdb")
        self.assertTrue(all(invocation.args[0] == "primary-0" for invocation in execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
