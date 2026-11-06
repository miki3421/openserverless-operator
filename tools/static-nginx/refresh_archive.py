# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace the bound static image manifest, preserving all other archive members."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'nginx-static/nginx-static-sts.yaml'
before = '0c5d842f2711c789a4d7aab6cba1e82f5ef50618086aa46dcfdd6fc2ff2fd317'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Duplicate deploy archive member')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != before:
        raise RuntimeError('Unexpected base static deployment')
    with tarfile.open(root / 'deploy.static.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.static.tar', root / 'deploy.tar')
