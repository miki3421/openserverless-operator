# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from openserverless import certmanager as cm


class CertificateBootstrap(unittest.TestCase):
    def fresh(self, *args, **kwargs):
        if args[0] == "get": return ""
        if args[0] == "version": return json.dumps({"serverVersion": {"major": "1", "minor": "36"}})
        return "ok"

    @patch.object(cm.cfg, "put")
    def test_fresh_install_uses_server_side_without_force_and_waits_for_all(self, put):
        with patch.object(cm.kube, "kubectl", side_effect=self.fresh) as run:
            self.assertEqual(cm.create(), "ok")
        apply = [c for c in run.call_args_list if c.args[0] == "apply"]
        self.assertEqual(len(apply), 1)
        self.assertIn("--server-side", apply[0].args)
        self.assertNotIn("--force-conflicts", apply[0].args)
        waits = [c.args[2] for c in run.call_args_list if c.args[0] == "rollout"]
        self.assertEqual(waits, ["deployment/cert-manager", "deployment/cert-manager-cainjector", "deployment/cert-manager-webhook"])

    def test_existing_service_is_preserved(self):
        with patch.object(cm.kube, "kubectl", return_value="service/cert-manager") as run:
            self.assertIn("skipping", cm.create())
            self.assertEqual(run.call_count, 1)

    def test_auth_failure_never_applies(self):
        with patch.object(cm.kube, "kubectl", side_effect=RuntimeError("Forbidden")) as run:
            with self.assertRaisesRegex(RuntimeError, "Forbidden"): cm.create()
            self.assertEqual(run.call_count, 1)

    def test_partial_namespace_or_crds_refused(self):
        for results in (["", "namespace/cert-manager"], ["", "", "crd/certificates.cert-manager.io"]):
            with patch.object(cm.kube, "kubectl", side_effect=results) as run:
                with self.assertRaisesRegex(RuntimeError, "refusing overwrite"): cm.create()
                self.assertFalse(any(c.args[0] == "apply" for c in run.call_args_list))

    @patch.object(cm.cfg, "put")
    def test_readiness_failure_propagates(self, put):
        def failure(args0, *args, **kwargs):
            if args0 == "rollout": raise RuntimeError("deployment not ready")
            return self.fresh(args0, *args, **kwargs)
        with patch.object(cm.kube, "kubectl", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "not ready"): cm.create()

    def test_manifest_and_task_catalog_match(self):
        import hashlib
        root = Path(__file__).resolve().parents[1]
        manifest = (root / "deploy/cert-manager/cert-manager.yaml").read_bytes()
        release = json.loads((root / "deploy/cert-manager/release.json").read_text())
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), release["manifestSha256"])
        for image in release["images"].values(): self.assertEqual(manifest.decode().count(image), 1)
        task = root.parent / "oplugins"
        if task.is_dir():
            self.assertEqual(manifest, (task / "util/cert-manager/cert-manager.yaml").read_bytes())
            catalog = json.loads((task / "opsroot.json").read_text())["config"]["images"]
            for key, image in release["images"].items(): self.assertEqual(catalog[key], image)

    def test_ready_pods_cannot_hide_broken_admission(self):
        with patch.object(cm.kube, "kubectl", side_effect=RuntimeError("TLS rejected")) as run:
            with self.assertRaisesRegex(RuntimeError, "admission is not ready"):
                cm.wait_for_admission(timeout=0)
            self.assertIn("--dry-run=server", run.call_args.args)
            self.assertIn("generateName", json.loads(run.call_args.kwargs["input"])["metadata"])
