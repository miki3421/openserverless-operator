# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Fresh KRaft only: a restart must never turn legacy Kafka into a migration."""
import json
import re
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / 'files/kafka-profile.json').read_text())


def validate_existing(existing, pvcs):
    relevant = [p for p in pvcs['items'] if p['metadata']['name'] in
                (PROFILE['claim'], 'kafka-pvc-data') or p['metadata'].get('labels', {}).get('app') == 'kafka']
    if not existing:
        if relevant:
            raise RuntimeError('Existing Kafka PVC without a qualified StatefulSet; explicit recovery is required')
        return
    spec = existing.get('spec', {})
    pod = spec.get('template', {}).get('spec', {})
    server = next((c for c in pod.get('containers', []) if c.get('name') == 'kafka'), {})
    if (server.get('image') != PROFILE['image'] or spec.get('replicas') != 1
            or server.get('command') != ['/opt/ops-kafka/start.sh']
            or pod.get('securityContext', {}).get('fsGroup') != PROFILE['uid']
            or server.get('securityContext', {}).get('runAsUser') != PROFILE['uid']
            or spec.get('template', {}).get('metadata', {}).get('labels', {}).get('openserverless.org/kafka-profile') != PROFILE['mode']):
        raise RuntimeError('Existing Kafka image/topology requires explicit migration; nothing was applied')
    current_claims = [p for p in relevant if p['metadata']['name'] == PROFILE['claim']]
    if (len(current_claims) != 1 or current_claims[0].get('status', {}).get('phase') != 'Bound'
            or current_claims[0]['metadata'].get('labels', {}).get('openserverless.org/kafka-profile') != PROFILE['mode']):
        raise RuntimeError('Existing Kafka storage is missing or unverified; explicit recovery is required')
    volumes = [v for v in pod.get('volumes', []) if v.get('name') == 'kafka-pvc']
    mounts = [m for m in server.get('volumeMounts', []) if m.get('name') == 'kafka-pvc']
    if (len(volumes) != 1 or len(mounts) != 1
            or volumes[0].get('persistentVolumeClaim', {}).get('claimName') != PROFILE['claim']
            or volumes[0].get('persistentVolumeClaim', {}).get('readOnly', False)
            or mounts[0].get('mountPath') != PROFILE['mountPath']
            or mounts[0].get('readOnly', False)
            or any(k in mounts[0] for k in ('subPath', 'subPathExpr'))
            or any(p['metadata']['name'] != PROFILE['claim'] for p in relevant)):
        raise RuntimeError('Existing Kafka volume layout requires explicit migration; nothing was applied')


def preflight(kubectl):
    if not re.fullmatch(r'docker\.io/miki3421/ops-advanced-kafka:[A-Za-z0-9_.-]+@sha256:[a-f0-9]{64}', PROFILE['image']):
        raise RuntimeError('Kafka image has not been qualified and pinned')
    raw = kubectl('get', 'statefulset', 'kafka', '--ignore-not-found', '-o', 'json')
    pvcs = kubectl('get', 'pvc', '-o', 'json')
    validate_existing(json.loads(raw) if raw.strip() else None, json.loads(pvcs))
