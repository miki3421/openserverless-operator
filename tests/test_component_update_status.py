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

"""Component update results must be submitted through the real Kopf patch."""
import unittest
from unittest.mock import patch
import kopf
from openserverless import main

class ComponentUpdateStatusTests(unittest.TestCase):
    def test_component_update_persists_result_without_losing_other_components(self):
        for result in ['on','off','error']:
            with self.subTest(result=result):
                state={'whisk_create':{'cron':'off','controller':'Ready'}}
                mutation=kopf.Patch()
                def apply_component(diff,status,*args):status['whisk_create']['cron']=result
                with patch.object(main.operator_util,'config_from_spec'), patch.object(main.kube,'get',return_value={'metadata':{'uid':'fixture'}}), \
                     patch('openserverless.image_catalog.reconcile'), patch.object(main.messaging_security,'reconcile'), \
                     patch.object(main.monitoring,'reconcile'), patch.object(main.seaweedfs,'reconcile'), \
                     patch.object(main.patcher,'patch',side_effect=apply_component):
                    main.whisk_update({},state,'openserverless',[],'controller',patch=mutation)
                self.assertEqual(mutation.status['whisk_create'],{'cron':result,'controller':'Ready'})

if __name__=='__main__':unittest.main()
