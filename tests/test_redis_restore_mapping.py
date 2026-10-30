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

"""Restored named Redis users must not disappear when the target pod restarts."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from jinja2 import Template
import yaml

ROOT = Path(__file__).resolve().parents[1]


def configuration(values):
    node = next(n for n in ast.parse((ROOT / 'openserverless/util.py').read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'get_redis_config_data')
    namespace = {'cfg': SimpleNamespace(get=lambda key, *args: values.get(key, args[-1] if args else None)),
                 'get_enable_pod_security': lambda: False, 'redis_affinity_tolerations_data': lambda data: None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'util.py', 'exec'), namespace)
    return namespace['get_redis_config_data']()


class RedisRestoreMappingTests(unittest.TestCase):
    def render(self, values):
        doc = yaml.safe_load(Template((ROOT / 'openserverless/templates/redis-conf.yaml').read_text()).render(**configuration(values)))
        return doc['data']['redis-conf']

    def test_legacy_configuration_does_not_require_an_acl_file(self):
        self.assertNotIn('aclfile ', self.render({}))

    def test_explicit_restored_acl_file_remains_on_persistent_volume(self):
        text = self.render({'redis.persistence-enabled': True, 'redis.acl-persistence-enabled': True})
        self.assertIn('aclfile /bitnami/redis/data/users.acl', text)
        self.assertIn('dir /bitnami/redis/data', text)

    def test_acl_state_cannot_be_requested_on_ephemeral_storage(self):
        with self.assertRaises(ValueError):
            configuration({'redis.acl-persistence-enabled': True})


if __name__ == '__main__':
    unittest.main()
