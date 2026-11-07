# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml
from openserverless import zookeeper
from openserverless.zookeeper_profile import PROFILE, validate_existing, preflight

ROOT = Path(__file__).parents[1]


class ZooKeeperProfileTests(unittest.TestCase):
    def current(self):
        return next(d for d in yaml.safe_load_all((ROOT / "deploy/zookeeper/zookeeper-003-sts.yaml").read_text()) if d)

    def test_fresh_and_matching_repeat(self):
        validate_existing(None, {"items": []})
        validate_existing(self.current(), {"items": [{"metadata": {"name": c}} for c in PROFILE["claims"]]})

    def test_legacy_image_and_orphaned_claims_refused(self):
        old = self.current();old["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/nuvolaris/zookeeper:3.4.14"
        with self.assertRaises(RuntimeError):validate_existing(old, {"items": []})
        for claim in PROFILE["claims"]:
            with self.assertRaises(RuntimeError):validate_existing(None, {"items": [{"metadata": {"name": claim}}]})

    def test_only_exact_qualified_patch_source_may_reuse_the_same_layout(self):
        for image in PROFILE["compatiblePatchImages"]:
            current = self.current()
            current["spec"]["template"]["spec"]["containers"][0]["image"] = image
            validate_existing(current, {"items": []})
            for bad in (image.split("@")[0], image[:-1] + ("0" if image[-1] != "0" else "1")):
                current["spec"]["template"]["spec"]["containers"][0]["image"] = bad
                with self.assertRaises(RuntimeError):validate_existing(current, {"items": []})

    def test_qualified_patch_image_does_not_bypass_mount_guards(self):
        for image in PROFILE["compatiblePatchImages"]:
            current = self.current()
            server = current["spec"]["template"]["spec"]["containers"][0]
            server["image"] = image
            server["volumeMounts"][1]["subPath"] = "unqualified"
            with self.assertRaises(RuntimeError):validate_existing(current, {"items": []})

    def test_different_topology_and_volume_layout_refused(self):
        bad = self.current();bad["spec"]["replicas"] = 3
        with self.assertRaises(RuntimeError):validate_existing(bad, {"items": []})
        for field, value in [("mountPath", "/legacy"), ("subPath", "other"), ("subPathExpr", "other"), ("readOnly", True)]:
            bad = self.current();bad["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][1][field] = value
            with self.assertRaises(RuntimeError):validate_existing(bad, {"items": []})
        bad = self.current();bad["spec"]["template"]["spec"]["volumes"][1]["persistentVolumeClaim"]["claimName"] = "other-data"
        with self.assertRaises(RuntimeError):validate_existing(bad, {"items": []})

    def test_preflight_only_reads_and_denial_precedes_apply(self):
        reads = []
        def kube(*args):
            reads.append(args)
            return "" if args[1] == "statefulset" else json.dumps({"items": []})
        preflight(kube)
        self.assertTrue(all(args[0] == "get" for args in reads))
        with patch.object(zookeeper.profile, "preflight", side_effect=RuntimeError("legacy volume")), patch.object(zookeeper.security, "apply") as apply:
            with self.assertRaises(RuntimeError):zookeeper.create()
            apply.assert_not_called()

    def test_server_keeps_admin_http_disabled_and_restricted_probe_commands(self):
        cfg = yaml.safe_load((ROOT / "deploy/zookeeper/zookeeper-000-cm.yaml").read_text())["data"]["zoo.cfg"]
        self.assertIn("admin.enableServer=false", cfg)
        self.assertIn("4lw.commands.whitelist=ruok,srvr", cfg)
        self.assertNotIn("server.0=", cfg)
        pod = self.current()["spec"]["template"]["spec"]
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertFalse(pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"])
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
