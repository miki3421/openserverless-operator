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

"""Exercise the operator's real Kopf loader in a fresh interpreter."""
from pathlib import Path
import shlex
import subprocess
import sys
import unittest


class OperatorModuleLoadingTests(unittest.TestCase):
    def test_launcher_imports_handler_modules_without_shadowing_redis_client(self):
        root = Path(__file__).resolve().parents[1]
        command = next(line.strip()[5:] for line in (root / 'run.sh').read_text().splitlines()
                       if line.strip().startswith('then exec '))
        args = shlex.split(command)
        modules = [args[i + 1] for i, arg in enumerate(args) if arg == '-m']
        self.assertEqual(['openserverless.main', 'openserverless.user_handlers',
                          'openserverless.workflows'], modules)
        result = subprocess.run([sys.executable, '-c',
            'from kopf._cogs.helpers.loaders import preload; '
            f'preload([], {modules!r}); '
            'import redis; from openserverless import redis as deployment; '
            'assert deployment.redislib.Redis is redis.Redis'],
            cwd=root, text=True, capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
