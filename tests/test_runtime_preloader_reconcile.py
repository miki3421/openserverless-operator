# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import unittest
from unittest import mock

import openserverless.patcher as patcher
import openserverless.runtimes_preloader as preloader


class RuntimePreloaderReconcileTests(unittest.TestCase):
    def test_success_deletes_then_recreates_preloader(self):
        owner = {"metadata": {"name": "controller"}}
        calls = []

        with mock.patch.object(
            patcher.preloader, "delete", side_effect=lambda value: calls.append(("delete", value))
        ), mock.patch.object(
            patcher.preloader, "create", side_effect=lambda value: calls.append(("create", value))
        ):
            patcher.patch_preloader(owner)

        self.assertEqual(calls, [("delete", owner), ("create", owner)])

    def test_failure_is_visible_to_kopf_for_retry(self):
        failure = RuntimeError("synthetic preloader failure")

        with mock.patch.object(patcher.preloader, "delete", side_effect=failure), mock.patch.object(
            patcher.preloader, "create"
        ) as create, self.assertLogs(level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "synthetic preloader failure"):
                patcher.patch_preloader({"metadata": {"name": "controller"}})

        create.assert_not_called()
        self.assertIn("failed to patch openwhisk runtime preloader", "\n".join(logs.output))


class RuntimePreloaderDeleteTests(unittest.TestCase):
    def test_owner_delete_does_not_require_previous_kustomize_render(self):
        with mock.patch.object(preloader.kus, "build") as build, mock.patch.object(
            preloader.kube, "delete", return_value="deleted"
        ) as delete:
            result = preloader.delete_by_owner()

        build.assert_not_called()
        delete.assert_called_once_with({
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "preload-runtimes", "namespace": "openserverless"},
        })
        self.assertEqual(result, "deleted")


if __name__ == "__main__":
    unittest.main()
