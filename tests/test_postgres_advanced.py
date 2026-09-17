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
#
# this module wraps kubectl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml
from jinja2 import Environment, StrictUndefined
from openserverless.postgres_profile import PROFILE, preflight, validate_existing

ROOT = Path(__file__).resolve().parents[1]


class AdvancedPostgresTests(unittest.TestCase):
    def test_fresh_install(self):
        validate_existing(None, {"items": []})

    def test_rejects_legacy_major(self):
        with self.assertRaisesRegex(RuntimeError, "explicit migration"):
            validate_existing({"spec": {"image": "pgvector/pgvector:pg16"}}, {"items": []})

    def test_rejects_old_layout_even_with_new_image(self):
        with self.assertRaisesRegex(RuntimeError, "data layout"):
            validate_existing({"spec": {"image": PROFILE["image"], "database": {"volumeMount": "/var/lib/postgresql/data"}}}, {"items": []})

    def test_repeated_install(self):
        validate_existing({"spec": {"image": PROFILE["image"], "database": {"volumeMount": PROFILE["volume_mount"]}}}, {"items": []})

    def test_orphaned_volumes_are_not_reused(self):
        with self.assertRaisesRegex(RuntimeError, "PVCs"):
            validate_existing(None, {"items": [{"metadata": {"name": "postgres-db-openserverless-postgres-1-0"}}]})
        validate_existing(None, {"items": [{"metadata": {"name": "unrelated"}}]})

    def test_preflight_is_read_only_and_does_not_ignore_api_errors(self):
        calls = []
        def read(*args, **kwargs):
            calls.append(args)
            return "" if args[1] == "crd" else '{"items": []}'
        preflight(read)
        self.assertTrue(all(args[0] == "get" for args in calls))
        def denied(*args, **kwargs):
            raise RuntimeError("forbidden")
        with self.assertRaisesRegex(RuntimeError, "forbidden"):
            preflight(denied)

    def test_manifest_and_backup_images_match_profile(self):
        data = dict(replicas=2, size=1, storageClass="standard", failover=True, affinity=False, tolerations=False, schedule="30 * * * *", dir="/var/lib/backup")
        env = Environment(undefined=StrictUndefined)
        for relative in ["openserverless/templates/postgres.yaml", "deploy/postgres-operator-deploy/postgres.yaml"]:
            obj = yaml.safe_load(env.from_string((ROOT / relative).read_text()).render(data))
            self.assertEqual(obj["spec"]["image"], PROFILE["image"])
            self.assertEqual(obj["spec"]["database"]["volumeMount"], PROFILE["volume_mount"])
        obj = yaml.safe_load(env.from_string((ROOT / "openserverless/templates/postgres-backup-sts.yaml").read_text()).render(data))
        self.assertEqual(obj["spec"]["template"]["spec"]["containers"][0]["image"], PROFILE["backup_image"])

    def test_failed_dump_does_not_publish_archive(self):
        config = yaml.safe_load((ROOT / "deploy/postgres-backup/postgres-backup-conf.yaml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fake = path / "pg_dumpall"
            fake.write_text("#!/bin/sh\necho incomplete\nexit 7\n")
            fake.chmod(0o700)
            result = subprocess.run(["bash", "-c", config["data"]["backup_database.sh"]], env={**os.environ, "PATH": directory + ":" + os.environ["PATH"], "KUBEGRES_RESOURCE_NAME": "test", "BACKUP_DESTINATION_FOLDER": directory, "BACKUP_SOURCE_DB_HOST_NAME": "unused"}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(path.glob("*.gz*")), [])
            fake.write_text("#!/bin/sh\necho complete\n")
            result = subprocess.run(["bash", "-c", config["data"]["backup_database.sh"]], env={**os.environ, "PATH": directory + ":" + os.environ["PATH"], "KUBEGRES_RESOURCE_NAME": "test", "BACKUP_DESTINATION_FOLDER": directory, "BACKUP_SOURCE_DB_HOST_NAME": "unused"}, capture_output=True)
            self.assertEqual(result.returncode, 0)
            archives = list(path.glob("*.gz"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
