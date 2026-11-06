# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only the bound Alertmanager manifest in the qualified parent archive."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'alert-manager/alertmanager-03-dep.yaml'
expected = 'e53114089b373518fa81294688d2571694a386e7b60dad10e55ced22c22bb6a8'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected duplicate archive members')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != expected:
        raise RuntimeError('Unexpected parent Alertmanager manifest')
    with tarfile.open(root / 'deploy.alertmanager.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.alertmanager.tar', root / 'deploy.tar')
