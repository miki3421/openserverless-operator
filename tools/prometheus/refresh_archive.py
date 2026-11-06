# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only the bound Prometheus manifest in the qualified parent archive."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'monitoring/prometheus-03-dep.yaml'
expected = '2790e65a4060e470c23d9fabf5c242e276b9c8be93196d54bb353797ab565626'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected duplicate archive members')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != expected:
        raise RuntimeError('Unexpected parent Prometheus manifest')
    with tarfile.open(root / 'deploy.prometheus.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.prometheus.tar', root / 'deploy.tar')
