# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Update only the bound Kubegres manifest in the incremental lab deploy archive."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'postgres-operator/kubegres-operator.yaml'
before = 'cfada6c2ea0192cc74f0f8859663abf29ce1084fe858d9c0ccbeed385398fa4c'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Duplicate deploy archive member')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != before:
        raise RuntimeError('Unexpected base Kubegres deployment')
    with tarfile.open(root / 'deploy.rbacproxy.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.rbacproxy.tar', root / 'deploy.tar')
