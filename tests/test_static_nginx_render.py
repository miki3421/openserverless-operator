# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Exercise the real static-provider renderer; templates must not undo deploy pins."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml
from openserverless import storage_static

ROOT = Path(__file__).resolve().parents[1]


class StaticNginxRenderTests(unittest.TestCase):
    def test_create_keeps_the_qualified_image_after_template_patches(self):
        expected = yaml.safe_load((ROOT / 'deploy/nginx-static/nginx-static-sts.yaml').read_text())
        wanted = expected['spec']['template']['spec']['containers'][0]['image']
        data = {'name': 'openserverless-static', 'container': 'openserverless-static',
                'size': 1, 'storageClass': 'synthetic', 'dir': '/var/cache/nginx',
                'storage_url': 'http://seaweedfs.openserverless.svc.cluster.local:9000',
                'affinity': False, 'tolerations': False, 'applypodsecurity': False}
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'deploy/nginx-static', root / 'deploy/nginx-static')
            shutil.copytree(ROOT / 'openserverless/templates', root / 'openserverless/templates')
            try:
                os.chdir(root)
                with patch.object(storage_static.util, 'get_storage_static_config_data', return_value=data), \
                     patch.object(storage_static.cfg, 'get', return_value='k3s'), \
                     patch.object(storage_static.cfg, 'put'), \
                     patch.object(storage_static.util, 'wait_for_pod_ready'), \
                     patch.object(storage_static, 'create_nuv_static_ingress'), \
                     patch.object(storage_static.kube, 'apply', side_effect=lambda spec: spec):
                    result = storage_static.create()
            finally:
                os.chdir(previous)
        sts = next(item for item in result['items'] if item['kind'] == 'StatefulSet')
        self.assertEqual(sts['spec']['template']['spec']['containers'][0]['image'], wanted)
        self.assertEqual(sts['spec']['template']['spec']['containers'][0]['ports'],
                         expected['spec']['template']['spec']['containers'][0]['ports'])
        self.assertEqual(sts['spec']['template']['spec']['volumes'],
                         expected['spec']['template']['spec']['volumes'])
