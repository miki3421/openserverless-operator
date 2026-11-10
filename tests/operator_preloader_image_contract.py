#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Validate the operator runtime-preloader assets and reconcile path in an image."""
import argparse
import json
from pathlib import Path
import subprocess


PRELOADER_PROBE = r'''
import json
import openserverless.runtimes_preloader as preloader

catalog = {
    "runtimes": {
        "python": [
            {
                "kind": "python:3.12",
                "default": True,
                "image": {"prefix": "registry.invalid", "name": "python", "tag": "3.12"},
            },
            {
                "kind": "python:sys",
                "default": False,
                "image": {"prefix": "registry.invalid", "name": "python", "tag": "sys-fixed"},
            },
        ]
    }
}
applied = []
deleted = []
preloader.util.get_runtimes_json_from_config_map = lambda: json.dumps(catalog)
preloader.cfg.put = lambda *args, **kwargs: None
preloader.kube.apply = lambda spec: applied.append(spec) or spec
preloader.kube.delete = lambda spec: deleted.append(spec) or spec
preloader.create()
preloader.delete_by_owner()
rendered = applied[0]["items"][0]
if rendered.get("kind") == "List":
    rendered = rendered["items"][0]
containers = rendered["spec"]["template"]["spec"]["containers"]
images = {container["name"]: container["image"] for container in containers}
expected = {
    "python": "registry.invalid/python:3.12",
    "python-sys": "registry.invalid/python:sys-fixed",
}
assert {name: images.get(name) for name in expected} == expected
assert deleted
deleted_job = deleted[0]
if isinstance(deleted_job, dict):
    assert deleted_job.get("kind") == "Job"
    assert deleted_job.get("metadata", {}).get("name") == "preload-runtimes"
else:
    assert "kind: Job" in deleted_job
print(json.dumps({"createdImages": images, "deleteManifest": "PASS"}))
'''


def run(image, entrypoint, command):
    return subprocess.check_output(
        [
            "docker", "run", "--rm", "--pull=never", "--network", "none",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--entrypoint", entrypoint, image, *command,
        ],
        text=True,
        timeout=120,
    )


def inventory(image):
    rows = run(image, "/bin/sh", ["-ec", "dpkg-query -W -f='${binary:Package}\\t${Version}\\n'"])
    return dict(row.split("\t", 1) for row in rows.splitlines())


def image_config(image):
    data = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
    return {key: data["Config"].get(key) for key in (
        "User", "ExposedPorts", "Env", "Entrypoint", "Cmd", "WorkingDir",
        "StopSignal", "Healthcheck", "Shell",
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    checks = []

    def check(name, passed, evidence=None):
        row = {"check": name, "status": "PASS" if passed else "FAIL"}
        if evidence is not None:
            row["evidence"] = evidence
        checks.append(row)

    base_packages = inventory(args.base)
    candidate_packages = inventory(args.candidate)
    changes = {
        name: [base_packages.get(name), candidate_packages.get(name)]
        for name in base_packages.keys() | candidate_packages.keys()
        if base_packages.get(name) != candidate_packages.get(name)
    }
    check("no_package_delta", not changes, changes)
    check("runtime_configuration_preserved", image_config(args.base) == image_config(args.candidate))

    assets = run(
        args.candidate,
        "/bin/sh",
        [
            "-ec",
            "test -f deploy/runtimes/runtimes-preload.yaml; "
            "test -f openserverless/templates/runtimes-job-container-attach.yaml; "
            "test -f deploy.tar; "
            "tar -tf deploy.tar | grep -qx 'runtimes/runtimes-preload.yaml'; "
            "sha256sum deploy/runtimes/runtimes-preload.yaml "
            "openserverless/templates/runtimes-job-container-attach.yaml",
        ],
    )
    check("runtime_preloader_assets_and_archive", True, assets.splitlines())

    probe = json.loads(run(
        args.candidate,
        "/home/openserverless/.venv/bin/python",
        ["-c", PRELOADER_PROBE],
    ))
    check("create_and_delete_render_path", probe.get("deleteManifest") == "PASS", probe)

    report = {
        "base": args.base,
        "candidate": args.candidate,
        "packageChanges": changes,
        "checks": checks,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if all(row["status"] == "PASS" for row in checks) else 1)


if __name__ == "__main__":
    main()
