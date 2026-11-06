# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Validate OPS notification templates without sending any notification."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--amtool', required=True, help='Candidate binary; run this test in a container with network disabled')
    args = parser.parse_args()
    template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
        (ROOT / 'openserverless/templates/alert-manager-02-cm.yaml').read_text())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o755)
        for label, slack, gmail, slack_default, gmail_default in [
            ('discard', False, False, False, False), ('slack', True, False, True, False),
            ('gmail', False, True, False, True), ('both_slack', True, True, True, False),
            ('both_gmail', True, True, False, True)]:
            rendered = template.render(slack=slack, gmail=gmail, slack_default=slack_default,
                                       gmail_default=gmail_default, slack_api_url='https://hooks.invalid/synthetic',
                                       slack_channel_name='#synthetic', email_recipients='recipient@example.invalid',
                                       email_from='sender@example.invalid', gmail_username='synthetic', gmail_password='synthetic')
            config = yaml.safe_load(rendered)['data']['alertmanager.yml']
            (root / 'config.yml').write_text(config)
            result = subprocess.run([args.amtool, '--enable-feature=utf8-strict-mode',
                                     'check-config', str(root / 'config.yml')],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(label + ': ' + result.stdout + result.stderr)
            print(json.dumps({'check': label + '_strict_config', 'status': 'PASS'}), flush=True)


if __name__ == '__main__': main()
