# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Bind a registry-only release layer to the qualified deployment archive."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile


def refresh(root, contract):
    with tarfile.open(root / "deploy.tar", "r:") as source:
        members = source.getmembers()
        if len(members) != contract["memberCount"] or len({m.name for m in members}) != len(members):
            raise ValueError("Unexpected deployment archive inventory")
        replacements = {}
        for name, digest in contract["members"].items():
            original = source.extractfile(name).read()
            if hashlib.sha256(original).hexdigest() != digest:
                raise ValueError("Parent deployment member differs: " + name)
            replacement = (root / "deploy" / name).read_bytes()
            expected = original.replace(b"localhost/ops-advanced", b"docker.io/miki3421/ops-advanced")
            if expected == original or replacement != expected:
                raise ValueError("Unexpected change beyond registry relocation: " + name)
            replacements[name] = replacement
        with tarfile.open(root / "deploy.registry.tar", "w") as target:
            for original in members:
                member = copy.copy(original)
                if member.name in replacements:
                    payload = replacements[member.name]
                    member.size = len(payload)
                    target.addfile(member, io.BytesIO(payload))
                else:
                    target.addfile(member, source.extractfile(member) if member.isfile() else None)
    os.replace(root / "deploy.registry.tar", root / "deploy.tar")


if __name__ == "__main__":
    refresh(Path("/home/openserverless"), json.loads(Path("/tmp/archive-inputs.json").read_text()))
