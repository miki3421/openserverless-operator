# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Resolve operator-managed images from Whisk.spec.images.

Only slot names live here; release image versions belong to opsroot.json.
Manifests retain their legacy defaults for Whisks without an image catalog.
Action/runtime and user workflow images are deliberately outside this resolver.
"""

from copy import deepcopy
import json
import re

import yaml
import openserverless.config as cfg


# A render group is bounded by the operator's deploy directory, not by an
# arbitrary container image/repository supplied by a user.
SLOTS = {
    'openwhisk-standalone': {'controller': 'controller'},
    'openwhisk-enterprise': {'controller': 'controller', 'check-kafka': 'busybox'},
    'openwhisk-invoker': {'invoker': 'invoker', 'check-kafka': 'busybox'},
    'couchdb': {'couchdb': 'couchdb', 'check-couchdb': 'busybox', 'init-couchdb': 'operator'},
    'redis': {'redis': 'redis'},
    'ferretdb': {'ferretdb': 'ferretdb'},
    'etcd': {'openserverless-etcd': 'etcd'},
    'kafka': {'kafka': 'kafka', 'volume-exporter': 'volume_exporter',
              'wait-for-zookeeper': 'busybox_kafka'},
    'zookeeper': {'zookeeper': 'zookeeper'},
    'nginx-static': {'openserverless-static': 'nginx', 'check-seaweedfs': 'busybox'},
    'seaweedfs': {'seaweedfs': 'seaweedfs'},
    'registry': {'registry': 'registry'},
    'postgres-operator': {'manager': 'kubegres', 'kube-rbac-proxy': 'rbacproxy'},
    'postgres-operator-deploy': {},
    'postgres-backup': {'openserverless-postgres-backup': 'pgdumper'},
    'monitoring': {'prometheus-server': 'prometheus',
                   'prometheus-server-configmap-reload': 'prometheus_reloader',
                   'kube-state-metrics': 'kube_state_metrics'},
    'alert-manager': {'alertmanager': 'alertmanager'},
    'scheduler': {'cron-init': 'operator'},
    'quota': {'quota-checker': 'operator'},
    'kvrocks': {'redis': 'kvrocks'},
    'milvus-operator': {'manager': 'milvus_operator'},
    'milvus': {'standalone': 'milvus'},
    'milvus-slim': {'standalone': 'milvus'},
    'cert-manager': {'cert-manager-controller': 'cert_manager',
                     'cert-manager-cainjector': 'cert_manager_cainjector',
                     'cert-manager-webhook': 'cert_manager_webhook'},
    'ingress-nginx': {'controller': 'ingress', 'create': 'ingress_admission',
                      'patch': 'ingress_admission'},
}

# Used only for image-only reconciliation of existing, Whisk-owned resources.
RESOURCES = {
    ('StatefulSet', 'controller'): 'openwhisk-enterprise',
    ('StatefulSet', 'invoker'): 'openwhisk-invoker',
    ('StatefulSet', 'couchdb'): 'couchdb',
    ('StatefulSet', 'redis'): 'redis',
    ('StatefulSet', 'openserverless-mongodb'): 'ferretdb',
    ('StatefulSet', 'openserverless-etcd'): 'etcd',
    ('StatefulSet', 'kafka'): 'kafka',
    ('StatefulSet', 'zookeeper'): 'zookeeper',
    ('StatefulSet', 'openserverless-static'): 'nginx-static',
    ('StatefulSet', 'seaweedfs'): 'seaweedfs',
    ('StatefulSet', 'registry'): 'registry',
    ('StatefulSet', 'openserverless-postgres-backup'): 'postgres-backup',
    ('StatefulSet', 'kvrocks'): 'kvrocks',
    ('Deployment', 'kubegres-controller-manager'): 'postgres-operator',
    ('Deployment', 'openserverless-prometheus-server'): 'monitoring',
    ('Deployment', 'openserverless-prometheus-kube-state-metrics'): 'monitoring',
    ('Deployment', 'openserverless-prometheus-alertmanager'): 'alert-manager',
    ('Deployment', 'openserverless-milvus-standalone'): 'milvus',
    ('Deployment', 'milvus-operator'): 'milvus-operator',
    ('CronJob', 'cron-init'): 'scheduler',
    ('CronJob', 'quota-checker'): 'quota',
    ('Kubegres', 'openserverless-postgres'): 'postgres-operator-deploy',
}
for _name in ('recovery', 'bookie', 'broker', 'proxy', 'zookeeper'):
    RESOURCES[('StatefulSet', 'openserverless-milvus-pulsarv3-' + _name)] = 'milvus'


def catalog():
    values = {key[len('images.'):]: value for key, value in cfg.getall('images.').items()}
    if not values and not cfg.exists('images'):
        return None  # An old installation: preserve its existing behavior.
    if not values:
        raise ValueError('spec.images must be a nonempty image catalog')
    for key, value in values.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]*', key):
            raise ValueError('Invalid spec.images key: ' + key)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]*', value) or '//' in value:
            raise ValueError('Invalid image reference for spec.images.' + key)
        if '@' in value:
            valid = re.fullmatch(r'.+@sha256:[0-9a-f]{64}', value)
        else:
            valid = re.fullmatch(r'.+:[A-Za-z0-9_][A-Za-z0-9_.-]*', value.rsplit('/', 1)[-1])
        if not valid:
            raise ValueError('A tag or sha256 digest is required for spec.images.' + key)
    return values


def validate_for_spec():
    """Check the enabled components before starting any deployment phase."""
    values = catalog()
    if values is None:
        return
    required = set()
    groups = {
        'openwhisk': 'openwhisk-enterprise' if cfg.get('components.invoker') else 'openwhisk-standalone',
        'invoker': 'openwhisk-invoker', 'couchdb': 'couchdb', 'redis': 'redis',
        'mongodb': 'ferretdb', 'etcd': 'etcd', 'kafka': 'kafka', 'zookeeper': 'zookeeper',
        'static': 'nginx-static', 'seaweedfs': 'seaweedfs', 'registry': 'registry',
        'cron': 'scheduler', 'quota': 'quota', 'monitoring': 'monitoring',
        'postgres': 'postgres-operator', 'milvus': 'milvus',
    }
    for component, group in groups.items():
        if cfg.get('components.' + component):
            required.update(SLOTS[group].values())
    if cfg.get('components.postgres'):
        required.add('pgvector')
        if cfg.get('postgres.backup.enabled'):
            required.add('pgdumper')
    if cfg.get('components.monitoring'):
        required.add('alertmanager')
    if cfg.get('components.milvus') and not cfg.get('openserverless.slim'):
        required.add('pulsar')
    if cfg.get('components.couchdb') and cfg.get('openserverless.kube') == 'openshift':
        required.discard('couchdb')
        required.add('couchdb_openshift')
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError('Missing spec.images entries for enabled components: ' + ', '.join(missing))


def pod_spec(resource):
    kind = resource.get('kind')
    spec = resource.get('spec', {})
    if kind == 'Pod':
        return spec, '/spec'
    if kind == 'CronJob':
        return spec['jobTemplate']['spec']['template']['spec'], '/spec/jobTemplate/spec/template/spec'
    if kind in ('StatefulSet', 'Deployment', 'DaemonSet', 'Job', 'ReplicaSet'):
        return spec['template']['spec'], '/spec/template/spec'
    return None, None


def image_slots(where, resource):
    """Yield (parent, field, catalog key, JSON-pointer) for managed slots."""
    if resource.get('kind') == 'Kubegres' and where == 'postgres-operator-deploy':
        yield resource['spec'], 'image', 'pgvector', '/spec/image'
        return
    pod, prefix = pod_spec(resource)
    if pod is None:
        return
    for field in ('initContainers', 'containers'):
        for index, container in enumerate(pod.get(field) or []):
            if 'image' not in container:
                continue
            name = container.get('name', '')
            key = SLOTS[where].get(name)
            if (where in ('milvus', 'milvus-slim') and key is None and
                    resource.get('metadata', {}).get('name', '').startswith('openserverless-milvus-pulsarv3-')):
                key = 'pulsar'
            if where == 'couchdb' and key == 'couchdb' and cfg.get('openserverless.kube') == 'openshift':
                key = 'couchdb_openshift'
            if where == 'ingress-nginx' and cfg.get('openserverless.kube') in ('kind', 'eks'):
                key = 'ingress_kind' if name == 'controller' else 'ingress_admission_kind'
            if key is None:
                raise ValueError('Unmapped image slot in ' + where + ': ' + name)
            yield container, 'image', key, prefix + '/' + field + '/' + str(index) + '/image'


def resolve(where, resource, values=None):
    """Return a copy. Never mutate caller-owned manifests or workflow images."""
    values = catalog() if values is None else values
    if values is None or where not in SLOTS:
        return resource
    result = deepcopy(resource)
    if result.get('kind') == 'List':
        result['items'] = [resolve(where, item, values) for item in result['items']]
        return result
    for parent, field, key, _ in image_slots(where, result):
        if key not in values:
            raise ValueError('Missing spec.images.' + key + ' for ' + where)
        parent[field] = values[key]
    return result


def render_yaml(where, text):
    values = catalog()
    if values is None or where not in SLOTS:
        return text
    return yaml.safe_dump_all([resolve(where, item, values) if isinstance(item, dict) else item
                               for item in yaml.safe_load_all(text)], sort_keys=False)


def reconcile(owner):
    """Patch image fields only; never recreate PVCs, Jobs, users or credentials.

    This is desired-image reconciliation, not a database migration engine.
    Callers must select versions compatible with the existing data format.
    """
    values = catalog()
    if values is None:
        return
    if not owner or not owner.get('metadata', {}).get('uid'):
        raise RuntimeError('Cannot reconcile images without the Whisk owner')
    import openserverless.kube as kube
    uid = owner['metadata']['uid']
    response = kube.kubectl('get', 'statefulsets,deployments,daemonsets,cronjobs', '-ojson', debugresult=False)
    resources = json.loads(response)['items']
    if cfg.get('components.postgres'):
        resources += json.loads(kube.kubectl('get', 'kubegres', '-ojson', debugresult=False))['items']
    pending = []
    for resource in resources:
        metadata = resource['metadata']
        where = RESOURCES.get((resource['kind'], metadata['name']))
        if where is None or not any(ref.get('uid') == uid for ref in metadata.get('ownerReferences', [])):
            continue
        # Validate every selected resource before issuing the first patch.
        resolved = resolve(where, resource, values)
        desired = {path: parent[field] for parent, field, _, path in image_slots(where, resolved)}
        changes = [{'op': 'replace', 'path': path, 'value': desired[path]}
                   for parent, field, _, path in image_slots(where, resource)
                   if parent[field] != desired[path]]
        if changes:
            pending.append((resource, changes))
    for resource, changes in pending:
        metadata = resource['metadata']
        patch = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': metadata['resourceVersion']}] + changes
        kube.kubectl('patch', resource['kind'] + '/' + metadata['name'], '--type=json',
                     '-p', json.dumps(patch), debugresult=False)
