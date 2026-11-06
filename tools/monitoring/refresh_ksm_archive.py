# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only the bound KSM manifest; preserve every other parent member."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
name = 'monitoring/kubestate-03-dep.yaml'
expected = 'aa846d7fa89e5a500a83a7441e8323f968155725a4d8d58ee0e019ccd4096b65'
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected duplicate archive members')
    if hashlib.sha256(source.extractfile(name).read()).hexdigest() != expected:
        raise RuntimeError('Unexpected parent KSM manifest')
    with tarfile.open(root / 'deploy.ksm.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name == name:
                payload = (root / 'deploy' / name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.ksm.tar', root / 'deploy.tar')
