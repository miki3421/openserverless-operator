# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Refuse implicit notification/silence upgrades and orphaned volume reuse on Advanced."""
import json
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / "files/alertmanager-profile.json").read_text())


def validate_existing(existing, pvcs):
    if not existing:
        if any(p["metadata"]["name"] == PROFILE["claim"] for p in pvcs["items"]):
            raise RuntimeError("Existing Alertmanager PVC without its Deployment: automatic volume reuse is refused. Explicit migration or restore is required.")
        return

    pod = existing.get("spec", {}).get("template", {}).get("spec", {})
    server = next((c for c in pod.get("containers", []) if c.get("name") == "alertmanager"), {})
    mounts = [m for m in server.get("volumeMounts", []) if m.get("name") == PROFILE["volume"]]
    volumes = [v for v in pod.get("volumes", []) if v.get("name") == PROFILE["volume"]]
    storage_args = [arg for arg in server.get("args", []) if arg.startswith("--storage.path")]
    valid = (
        server.get("image") == PROFILE["image"]
        and len(mounts) == 1
        and mounts[0].get("mountPath") == PROFILE["volume_mount"]
        and not any(k in mounts[0] for k in ("subPath", "subPathExpr"))
        and not mounts[0].get("readOnly", False)
        and len(volumes) == 1
        and volumes[0].get("persistentVolumeClaim", {}).get("claimName") == PROFILE["claim"]
        and not volumes[0].get("persistentVolumeClaim", {}).get("readOnly", False)
        and storage_args == ["--storage.path=" + PROFILE["volume_mount"]]
    )
    if not valid:
        raise RuntimeError("OPS Advanced Alertmanager requires an explicit migration: existing image or notification/silence volume layout differs. No monitoring resources were changed.")


def preflight(kubectl):
    raw = kubectl("get", "deployment", PROFILE["deployment"], "--ignore-not-found", "-o", "json")
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json"))
    validate_existing(json.loads(raw) if raw.strip() else None, pvcs)
