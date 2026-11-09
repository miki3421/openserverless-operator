# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import inspect
import os
from unittest import TestCase
from unittest.mock import patch

from openserverless import activation_retention, config, couchdb, cronjob
from openserverless import quota_checker_job


REFERENCE = ("docker.io/miki3421/ops-advanced-operator:"
             "20261109-pg-libxml2-r2@sha256:"
             "d417e356ba5c7a055e1e7c4d9ebcc7985ae3e5e785f2569ee46c298b1f31fe86")


class OperatorSelfReferenceTests(TestCase):
    def tearDown(self):
        config.clean()

    def test_exact_environment_reference_wins(self):
        with patch.dict(os.environ, {"OPERATOR_REFERENCE": REFERENCE}):
            self.assertEqual(config.operator_image_reference(), REFERENCE)

    def test_mutable_environment_reference_fails_closed(self):
        with patch.dict(os.environ, {"OPERATOR_REFERENCE": "example/operator:latest"}):
            with self.assertRaisesRegex(ValueError, "digest-qualified"):
                config.operator_image_reference()

    def test_missing_self_image_fails_closed(self):
        with patch.dict(os.environ, {"OPERATOR_IMAGE": "missing-OPERATOR_IMAGE",
                                     "OPERATOR_TAG": "missing-OPERATOR_TAG"},
                        clear=True):
            with self.assertRaisesRegex(ValueError, "required"):
                config.operator_image_reference()

    def test_all_operator_derived_jobs_use_central_reference(self):
        for module in (activation_retention, couchdb, cronjob, quota_checker_job):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("cfg.operator_image_reference()", source)
                self.assertNotIn("f\"{img}:{tag}\"", source)

