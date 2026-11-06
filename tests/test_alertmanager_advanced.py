# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
from openserverless import monitoring
from openserverless.alertmanager_profile import PROFILE, preflight, validate_existing

ROOT = Path(__file__).resolve().parents[1]


class AlertmanagerProfileTests(unittest.TestCase):
    def current(self):
        return yaml.safe_load((ROOT / "deploy/alert-manager/alertmanager-03-dep.yaml").read_text())

    def test_fresh_install_and_unrelated_pvc(self):
        validate_existing(None, {"items": []})
        validate_existing(None, {"items": [{"metadata": {"name": "unrelated"}}]})

    def test_repeat_same_profile(self):
        validate_existing(self.current(), {"items": [{"metadata": {"name": PROFILE["claim"]}}]})

    def test_explicit_writable_defaults_can_repeat(self):
        obj = self.current()
        pod = obj['spec']['template']['spec']
        pod['containers'][0]['volumeMounts'][1]['readOnly'] = False
        pod['volumes'][1]['persistentVolumeClaim']['readOnly'] = False
        validate_existing(obj, {"items": []})

    def test_legacy_and_changed_image_refused(self):
        for image in ["quay.io/prometheus/alertmanager:v0.25.0", "quay.io/prometheus/alertmanager:v0.34.2"]:
            obj = self.current()
            obj["spec"]["template"]["spec"]["containers"][0]["image"] = image
            with self.subTest(image=image), self.assertRaisesRegex(RuntimeError, "explicit migration"):
                validate_existing(obj, {"items": []})

    def test_changed_storage_refused(self):
        original = self.current()["spec"]["template"]["spec"]
        variants = []
        pod = copy.deepcopy(original)
        pod["volumes"][1]["persistentVolumeClaim"]["claimName"] = "other"
        variants.append(pod)
        pod = copy.deepcopy(original)
        pod["volumes"][1]["persistentVolumeClaim"]["readOnly"] = True
        variants.append(pod)
        pod = copy.deepcopy(original)
        pod["volumes"][1] = {"name": "storage-volume", "emptyDir": {}}
        variants.append(pod)
        for key, value in [("mountPath", "/other"), ("subPath", "legacy"), ("subPathExpr", "$(OLD)"), ("readOnly", True)]:
            pod = copy.deepcopy(original)
            pod["containers"][0]["volumeMounts"][1][key] = value
            variants.append(pod)
        pod = copy.deepcopy(original)
        pod["containers"][0]["args"].append("--storage.path=/other")
        variants.append(pod)
        for index, pod in enumerate(variants):
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, "volume layout"):
                validate_existing({"spec": {"template": {"spec": pod}}}, {"items": []})

    def test_orphaned_pvc_refused(self):
        with self.assertRaisesRegex(RuntimeError, "automatic volume reuse"):
            validate_existing(None, {"items": [{"metadata": {"name": PROFILE["claim"]}}]})

    def test_read_only_preflight(self):
        calls = []
        def read(*args):
            calls.append(args)
            return "" if args[1] == "deployment" else '{"items":[]}'
        preflight(read)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0] == "get" for call in calls))

    def test_api_and_invalid_json_errors_propagate(self):
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            preflight(lambda *a: (_ for _ in ()).throw(RuntimeError("Forbidden")))
        with self.assertRaises(json.JSONDecodeError):
            preflight(lambda *a: "invalid")

    def test_rejection_precedes_all_monitoring_mutations(self):
        with patch.object(monitoring.prometheus_profile, "preflight"), \
             patch.object(monitoring.alertmanager_profile, "preflight", side_effect=RuntimeError("explicit migration")), \
             patch.object(monitoring.cfg, "get", return_value=True), \
             patch.object(monitoring.cfg, "put") as save, \
             patch.object(monitoring.kus, "renderTemplate") as render, \
             patch.object(monitoring.kube, "apply") as apply:
            with self.assertRaisesRegex(RuntimeError, "explicit migration"):
                monitoring.create()
            save.assert_not_called()
            render.assert_not_called()
            apply.assert_not_called()

    def test_manifest_and_catalogue_agree(self):
        self.assertEqual(self.current()["spec"]["template"]["spec"]["containers"][0]["image"], PROFILE["image"])
        catalog = ROOT.parent / "oplugins/opsroot.json"
        if not catalog.exists():
            self.skipTest("task sibling not mounted in this build test")
        self.assertEqual(json.loads(catalog.read_text())["config"]["images"]["alertmanager"], PROFILE["image"])
