import unittest
from unittest.mock import patch
from openserverless import util


class ImageReferenceTests(unittest.TestCase):
    def test_tag_digest_and_registry_port_survive_template_reconstruction(self):
        for reference in (
            "registry.example/operator:release",
            "registry.example:5000/team/controller:release",
            "registry.example/controller@sha256:" + "a" * 64,
            "registry.example:5000/controller:release@sha256:" + "b" * 64,
        ):
            with self.subTest(reference=reference):
                result = util.parse_image(reference)
                self.assertEqual(result["image"] + ":" + result["tag"], reference)
                data = {}
                with patch.object(util.cfg, "get", return_value=reference):
                    util.get_controller_image_data(data)
                self.assertEqual(data["controller_image"] + ":" + data["controller_tag"], reference)

    def test_invalid_references_fail_before_render(self):
        for reference in ("", "repo/controller", "registry.example:5000/controller", "controller:",
                          "repo/controller@sha256:bad", "controller:tag\n", "controller:$(id)"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                util.parse_image(reference)


if __name__ == "__main__":
    unittest.main()
