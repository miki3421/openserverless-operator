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

"""A restored Milvus prefix must survive a normal operator configuration render."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from jinja2 import Template
import yaml

ROOT = Path(__file__).resolve().parents[1]


def configuration(values):
    tree = ast.parse((ROOT / 'openserverless/util.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_milvus_config_data')
    namespace = {'cfg': SimpleNamespace(get=lambda key: values.get(key)), 're': re,
                 'get_etcd_replica': lambda: 1, 'milvus_standalone_affinity_tolerations_data': lambda data: None}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'util.py', 'exec'), namespace)
    return namespace['get_milvus_config_data']()


class MilvusRestoreMappingTests(unittest.TestCase):
    def test_default_prefixes_are_compatible(self):
        data = configuration({})
        self.assertEqual(data['milvus_etcd_prefix'], 'milvus')
        self.assertEqual(data['milvus_storage_root_path'], 'files')
        self.assertEqual(data['milvus_etcd_username'], 'etcdmilvus')

    def test_restored_prefixes_survive_both_templates(self):
        data = configuration({'milvus.etcd.root-path': 'restored_metadata',
                              'milvus.storage.root-path': 'restored_vectors',
                              'milvus.etcd.username': 'restored_service'})
        for name in ['milvus-cfg-base.yaml', 'milvus-cfg-slim-base.yaml']:
            rendered = yaml.safe_load(Template((ROOT / 'openserverless/templates' / name).read_text()).render(**data))
            self.assertEqual(rendered['kind'], 'Secret')
            config = yaml.safe_load(rendered['stringData']['default.yaml'])
            self.assertEqual(config['etcd']['rootPath'], 'restored_metadata')
            self.assertEqual(config['etcd']['auth']['userName'], 'restored_service')
            self.assertEqual(config['minio']['rootPath'], 'restored_vectors')

    def test_unsafe_prefixes_and_root_identity_fail_before_deployment(self):
        for key in ['milvus.etcd.root-path', 'milvus.storage.root-path', 'milvus.etcd.username']:
            for value in ['../source', '/', 'value\nother', 'a/b', 42]:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    configuration({key: value})
        with self.assertRaises(ValueError):
            configuration({'milvus.etcd.username': 'root'})


if __name__ == '__main__':
    unittest.main()
