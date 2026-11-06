# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Replace only two known deploy.tar members; never extract an input archive."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
expected = {
    'openserverless-permissions/whisk-crd.yaml': '611538d93356236303cbb6acb97eda5833a0d256e5c582ecc0d2611d5b16076a',
    'openserverless-operator/whisk.yaml': '9794dd37d774756c6ab65a31b2fa8b313d0c9b72eaaa9d9b08941578130c09a0',
}
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Duplicate deploy archive member')
    for name, digest in expected.items():
        if hashlib.sha256(source.extractfile(name).read()).hexdigest() != digest:
            raise RuntimeError('Unexpected base deploy archive member')
    with tarfile.open(root / 'deploy.retention.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name in expected:
                payload = (root / 'deploy' / member.name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.retention.tar', root / 'deploy.tar')
