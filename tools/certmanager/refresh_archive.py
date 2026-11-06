# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace the bound cert-manager manifest and add its immutable release receipt."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'cert-manager/cert-manager.yaml'
receipt = 'cert-manager/release.json'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members) or receipt in {m.name for m in members}:
        raise RuntimeError('Unexpected base archive membership')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != '3eeda5b72789ee1da66167cb2f7839d32c514f0df9cb458bc51adaf55d6592e4':
        raise RuntimeError('Unexpected base cert-manager manifest')
    with tarfile.open(root / 'deploy.certmanager.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
        payload = (root / 'deploy' / receipt).read_bytes()
        member = tarfile.TarInfo(receipt)
        member.size = len(payload)
        member.mode = 0o644
        member.uid = member.gid = 1001
        target.addfile(member, io.BytesIO(payload))
os.replace(root / 'deploy.certmanager.tar', root / 'deploy.tar')
