# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile


root = Path("/home/openserverless")
contract = json.loads(Path("/tmp/archive-inputs.json").read_text())
with tarfile.open(root / "deploy.tar", "r:") as source:
    members = source.getmembers()
    if len(members) != contract["memberCount"] or len({m.name for m in members}) != len(members):
        raise ValueError("Unexpected deployment archive inventory")
    replacements = {}
    for name, digest in contract["members"].items():
        if hashlib.sha256(source.extractfile(name).read()).hexdigest() != digest:
            raise ValueError("Parent ZooKeeper manifest mismatch: " + name)
        payload = (root / "deploy" / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != contract["replacements"][name]:
            raise ValueError("Candidate ZooKeeper manifest mismatch: " + name)
        replacements[name] = payload
    with tarfile.open(root / "deploy.zookeeper.tar", "w") as target:
        for original in members:
            member = copy.copy(original)
            if member.name in replacements:
                payload = replacements[member.name]
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
            else:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
os.replace(root / "deploy.zookeeper.tar", root / "deploy.tar")
