# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Fresh single-server profile; never infer compatibility of legacy ZK volumes."""
import json
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / "files/zookeeper-profile.json").read_text())


def validate_existing(existing, pvcs):
    claims = PROFILE["claims"]
    if not existing:
        if any(p["metadata"]["name"] in claims for p in pvcs["items"]):
            raise RuntimeError("Existing ZooKeeper PVC without its StatefulSet; explicit recovery is required")
        return
    spec = existing.get("spec", {})
    pod = spec.get("template", {}).get("spec", {})
    containers = pod.get("containers", [])
    server = next((c for c in containers if c.get("name") == "zookeeper"), {})
    if len(containers) != 1 or server.get("image") != PROFILE["image"] or spec.get("replicas", 1) != 1:
        raise RuntimeError("Existing ZooKeeper image/topology requires explicit migration; nothing was applied")
    for claim, path in claims.items():
        volumes = [v for v in pod.get("volumes", []) if v.get("name") == claim]
        mounts = [m for m in server.get("volumeMounts", []) if m.get("name") == claim]
        valid = (len(volumes) == len(mounts) == 1
                 and volumes[0].get("persistentVolumeClaim", {}).get("claimName") == claim
                 and not volumes[0].get("persistentVolumeClaim", {}).get("readOnly", False)
                 and mounts[0].get("mountPath") == path
                 and not any(k in mounts[0] for k in ("subPath", "subPathExpr"))
                 and not mounts[0].get("readOnly", False))
        if not valid:
            raise RuntimeError("Existing ZooKeeper volume layout requires explicit migration; nothing was applied")


def preflight(kubectl):
    raw = kubectl("get", "statefulset", "zookeeper", "--ignore-not-found", "-o", "json")
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json"))
    validate_existing(json.loads(raw) if raw.strip() else None, pvcs)
