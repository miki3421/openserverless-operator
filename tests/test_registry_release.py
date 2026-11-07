# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("registry_release", Path(__file__).parents[1] / "tools/registry-release/refresh_archive.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RegistryReleaseTest(unittest.TestCase):
    def fixture(self, root):
        payloads = {"a.yaml": b"image: localhost/ops-advanced-a:qualified\n", "z.yaml": b"image: localhost/ops-advanced-z:qualified\n", "untouched": b"preserve bytes\n"}
        (root / "deploy").mkdir()
        with tarfile.open(root / "deploy.tar", "w") as target:
            for name, payload in payloads.items():
                member = tarfile.TarInfo(name);member.size = len(payload);member.mode = 0o640
                target.addfile(member, io.BytesIO(payload))
                (root / "deploy" / name).write_bytes(payload.replace(b"localhost/ops-advanced", b"docker.io/miki3421/ops-advanced"))
        return {"memberCount": 3, "members": {n: hashlib.sha256(payloads[n]).hexdigest() for n in ("a.yaml", "z.yaml")}}

    def test_relocation_preserves_other_member_bytes_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);contract = self.fixture(root)
            module.refresh(root, contract)
            with tarfile.open(root / "deploy.tar") as result:
                self.assertEqual(result.extractfile("untouched").read(), b"preserve bytes\n")
                self.assertEqual(result.getmember("untouched").mode, 0o640)
                self.assertEqual(result.extractfile("z.yaml").read(), b"image: docker.io/miki3421/ops-advanced-z:qualified\n")

    def test_last_parent_drift_preserves_original_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);contract = self.fixture(root);before = (root / "deploy.tar").read_bytes()
            contract["members"]["z.yaml"] = "0" * 64
            with self.assertRaises(ValueError):module.refresh(root, contract)
            self.assertEqual((root / "deploy.tar").read_bytes(), before)

    def test_non_registry_change_preserves_original_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);contract = self.fixture(root);before = (root / "deploy.tar").read_bytes()
            (root / "deploy/z.yaml").write_bytes(b"image: other/unqualified:changed\n")
            with self.assertRaises(ValueError):module.refresh(root, contract)
            self.assertEqual((root / "deploy.tar").read_bytes(), before)
