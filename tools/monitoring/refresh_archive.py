# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Update exactly two monitoring manifests in the qualified lab operator."""
import copy
import hashlib
import io
import os
from pathlib import Path
import tarfile

root = Path('/home/openserverless')
expected = {
    'monitoring/kubestate-03-dep.yaml': '309e3103ca41cb1a4de81d542c74af180b5326e266f9a5de951037ac81e9c171',
    'monitoring/prometheus-03-dep.yaml': 'aa2392a3f4941c3c24838c384fd727c98de342c9873f4e7c39285f54273f96e8',
}
with tarfile.open(root / 'deploy.tar', 'r:') as source:
    members = source.getmembers()
    if len({m.name for m in members}) != len(members):
        raise RuntimeError('Unexpected duplicate archive members')
    for name, digest in expected.items():
        if hashlib.sha256(source.extractfile(name).read()).hexdigest() != digest:
            raise RuntimeError('Unexpected parent manifest: ' + name)
    with tarfile.open(root / 'deploy.monitoring.tar', 'w') as target:
        for original in members:
            member = copy.copy(original)
            if member.name in expected:
                payload = (root / 'deploy' / member.name).read_bytes()
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / 'deploy.monitoring.tar', root / 'deploy.tar')
