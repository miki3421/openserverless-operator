# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only bound Kafka members in the qualified operator deployment archive."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
contract = json.loads(Path('/tmp/kafka-archive-inputs.json').read_text())
with tarfile.open(root / 'deploy.tar') as source:
    members = source.getmembers()
    if len(members) != contract['memberCount'] or len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected deployment archive inventory')
    for name, digest in contract['members'].items():
        if hashlib.sha256(source.extractfile(name).read()).hexdigest() != digest:
            raise RuntimeError('Unexpected parent deployment member: ' + name)
    with tarfile.open(root / 'deploy.kafka.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name in contract['members']:
                data = (root / 'deploy' / member.name).read_bytes()
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.kafka.tar', root / 'deploy.tar')
