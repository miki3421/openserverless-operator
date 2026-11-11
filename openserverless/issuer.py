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
#
import kopf, logging, json
import openserverless.kube as kube
import openserverless.kustomize as kus
import openserverless.config as cfg
import openserverless.operator_util as operator_util
import openserverless.util as util

def create(owner=None):
    logging.info(f"*** Configuring cluster issuer")
    issuer_name = util.tls_issuer_name()
    if cfg.get('tls.external-issuer'):
        # External PKI is provisioned independently. Never replace its spec,
        # create a fallback ACME issuer, read its key or adopt its ownership.
        resource = json.loads(kube.kubectl('get', 'clusterissuer', issuer_name, '-o', 'json', namespace=None, debugresult=False))
        if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in resource.get('status', {}).get('conditions', [])):
            raise ValueError(f'External ClusterIssuer {issuer_name} is not Ready')
        cfg.put('state.issuer.spec', None)
        return f'Using external ClusterIssuer {issuer_name}'
    # We deploy a cluster-issuer
    runtime = cfg.get('openserverless.kube')
    acme_registered_email = cfg.get('tls.acme-registered-email') or "nuvolaris@nuvolaris.io"
    acme_server_url = cfg.get('tls.acme-server-url') or "https://acme-staging-v02.api.letsencrypt.org/directory"
   
    issuer_class = "nginx"

    # On microk8s cluster issuer class must be public
    if runtime == "microk8s":
        issuer_class = "public"

    # On k3s cluster issuer class must be traefik
    if runtime == "k3s":
        issuer_class = "traefik"        
    
    logging.info(f"*** Configuring cluster issuer using email {acme_registered_email}")
    logging.info(f"*** Configuring cluster issuer using let's encrypt server {acme_server_url}")
  
    data = {
        "acme_registered_email": acme_registered_email,
        "acme_server_url": acme_server_url,
        "issuer_class":issuer_class,
        "name": "tls",
        "issuer_name": issuer_name,
        "runtime": runtime
    }
    
    kust = kus.patchTemplate("issuer", "cluster-issuer.yaml", data)
    spec = "deploy/issuer/__cluster-issuer.yaml"

    cfg.put("state.issuer.spec", spec)
    res = kube.kubectl("apply", "-f", spec,namespace=None)
    return res

def delete_by_owner():
    spec = "deploy/issuer/__cluster-issuer.yaml"
    res = kube.kubectl("delete", "-f", spec,namespace=None)
    logging.info(f"delete cluster issuer: {res}")
    return res

def delete_by_spec():
    spec = cfg.get("state.issuer.spec")
    res = False
    if spec:
        res = kube.kubectl("delete", "-f", spec,namespace=None)
        return res

def delete(owner=None):
    if cfg.get('tls.external-issuer'):
        logging.info('Preserving the externally managed ClusterIssuer')
        return False
    if owner:        
        return delete_by_owner()
    else:
        return delete_by_spec()

def patch(status, action, owner=None):
    """
    Called by the operator patcher to create/update/delete issuer component
    """
    try:
        logging.info(f"*** handling request to {action} certificate issuer")
        if  action == 'create':
            msg = create(owner)
            operator_util.patch_operator_status(status,'issuer','on')
        elif action == 'delete':
            msg = delete(owner)
            operator_util.patch_operator_status(status,'issuer','off')
        else:
            msg = create(owner)
            operator_util.patch_operator_status(status,'issuer','updated')

        logging.info(msg)        
        logging.info(f"*** handled request to {action} issuer") 
    except Exception as e:
        logging.error('*** failed to update issuer: %s' % e)
        operator_util.patch_operator_status(status,'issuer','error')          

