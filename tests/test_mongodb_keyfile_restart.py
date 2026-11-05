import os
import pathlib
import subprocess
import tempfile
import unittest

from openserverless.mongodb_profile import resources


class MongoKeyfileRestartTest(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, 'Run as a non-root user to reproduce mode-0400 restart behavior')
    def test_existing_read_only_keyfile_can_be_recreated_without_changing_bytes(self):
        spec = resources()
        items = spec['items'] if isinstance(spec, dict) else spec
        sts = next(x for x in items if x['kind'] == 'StatefulSet')
        command = sts['spec']['template']['spec']['initContainers'][0]['command'][-1]
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / 'source'
            target = pathlib.Path(root) / 'target'
            source.mkdir(); target.mkdir()
            expected = b'synthetic-keyfile-restart-fixture\n'
            (source / 'keyfile').write_bytes(expected)
            (source / 'keyfile').chmod(0o440)
            command = command.replace('/run/source', str(source)).replace('/run/target', str(target))
            for _ in range(2):
                subprocess.run(['sh', '-c', command], check=True, capture_output=True)
                self.assertEqual((target / 'keyfile').read_bytes(), expected)
                self.assertEqual((target / 'keyfile').stat().st_mode & 0o777, 0o400)
            self.assertEqual((source / 'keyfile').read_bytes(), expected)


if __name__ == '__main__':
    unittest.main()
