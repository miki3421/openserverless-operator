# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only the registry image manifest in the qualified lab operator."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'registry/02-registry-sts.yaml'
expected = '4fabae48e506b1a59e82d6205152d2c961c7577e999ec80d800614b1d889ec1d'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected duplicate archive members')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != expected:
        raise RuntimeError('Unexpected parent registry manifest')
    with tarfile.open(root / 'deploy.registry.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.registry.tar', root / 'deploy.tar')
