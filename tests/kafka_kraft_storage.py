# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Exercise the real image startup refusals using disposable, synthetic tmpfs data."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--image', required=True, help='Already cached candidate; never pulled')
args = parser.parse_args()
base = 'mkdir -p /kafka/kraft-logs; '
meta = 'version=1\\nnode.id=0\\ncluster.id=AAAAAAAAAAAAAAAAAAAAAA\\ndirectory.id=BBBBBBBBBBBBBBBBBBBBBB\\n'
cases = [
    ('foreign-layout', 'mkdir /kafka/kafka-logs-kafka-0', 'foreign_data_requires_explicit_migration'),
    ('unformatted', 'mkdir /kafka/kraft-logs; touch /kafka/kraft-logs/sentinel', 'nonempty_unformatted_storage'),
    ('legacy-metadata', base + "printf '" + meta.replace('version=1', 'version=0') + "' > /kafka/kraft-logs/meta.properties", 'non_kraft_or_invalid_metadata'),
    ('wrong-node', base + "printf '" + meta.replace('node.id=0', 'node.id=1') + "' > /kafka/kraft-logs/meta.properties", 'mismatched_node_identity'),
    ('duplicate-identity', base + "printf '" + meta + "cluster.id=CCCCCCCCCCCCCCCCCCCCCC\\n' > /kafka/kraft-logs/meta.properties", 'ambiguous_or_missing_metadata_key'),
    ('invalid-cluster', base + "printf '" + meta.replace('AAAAAAAAAAAAAAAAAAAAAA', 'bad') + "' > /kafka/kraft-logs/meta.properties", 'invalid_cluster_identity'),
    ('symlink-layout', 'ln -s /tmp /kafka/kraft-logs', 'foreign_data_requires_explicit_migration'),
]
report = []
for name, setup, reason in cases:
    result = subprocess.run([
        'docker', 'run', '--rm', '--pull=never', '--network', 'none', '--hostname', 'kafka-0',
        '--read-only', '--tmpfs', '/tmp:rw,exec,nosuid,size=64m',
        '--tmpfs', '/kafka:rw,uid=1000,gid=1000,mode=0770,size=16m',
        '--memory', '512m', '--cpus', '1', '--pids-limit', '128', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--entrypoint', '/bin/sh', args.image,
        '-c', setup + '; exec /opt/ops-kafka/start.sh'], capture_output=True, text=True, timeout=30)
    if result.returncode == 0 or reason not in result.stderr or 'initialize empty' in result.stdout:
        raise RuntimeError(f'{name}: unexpected startup result: {result.stdout} {result.stderr}')
    report.append({'check': name, 'status': 'PASS', 'reason': reason, 'formatInvoked': False})
print(json.dumps(report, indent=2))
