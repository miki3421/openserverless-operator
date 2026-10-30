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

"""The rendered SeaweedFS PVC must keep the explicitly selected storage class."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from jinja2 import Template
import yaml

ROOT = Path(__file__).resolve().parents[1]

class SeaweedfsRestoreMappingTests(unittest.TestCase):
    def test_retained_storage_class_reaches_the_existing_pvc_template(self):
        node = next(n for n in ast.parse((ROOT / 'openserverless/util.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'get_seaweedfs_config_data')
        values = {'openserverless.storageclass': 'retained-recovery', 'seaweedfs.volume-size': 60}
        namespace = {'cfg': SimpleNamespace(get=lambda key, *args: values.get(key)),
                     'get_enable_pod_security': lambda: False,
                     'seaweedfs_affinity_tolerations_data': lambda data: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'util.py', 'exec'), namespace)
        data = namespace['get_seaweedfs_config_data']()
        pvc = yaml.safe_load(Template((ROOT / 'openserverless/templates/pvc-attach.yaml').read_text()).render(**data))
        self.assertEqual(pvc['spec']['storageClassName'], 'retained-recovery')
        self.assertEqual(pvc['spec']['resources']['requests']['storage'], '60Gi')

if __name__ == '__main__':
    unittest.main()
