# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import json
import unittest
import yaml
from unittest.mock import patch
from openserverless import config as cfg, issuer, ingress_data, util
from openserverless.template import expand_template


class ExternalIssuerTests(unittest.TestCase):
    def settings(self, external=True, name="company-ca"):
        return patch.object(cfg, 'get', side_effect=lambda key, *args: {
            'tls.external-issuer': external, 'tls.issuer-name': name,
            'components.tls': True, 'openserverless.kube': 'k3s',
        }.get(key))

    def test_external_ready_issuer_is_only_read_and_never_deleted(self):
        resource={'status': {'conditions': [{'type':'Ready','status':'True'}]}}
        with self.settings(), patch.object(issuer.kube,'kubectl',return_value=json.dumps(resource)) as command, patch.object(issuer.kus,'patchTemplate') as render:
            self.assertIn('company-ca',issuer.create())
            command.assert_called_once_with('get','clusterissuer','company-ca','-o','json',namespace=None,debugresult=False)
            self.assertFalse(issuer.delete(owner={}))
            self.assertEqual(command.call_count,1)
            render.assert_not_called()

    def test_external_missing_unready_or_api_denied_never_falls_back_to_acme(self):
        with self.settings(),patch.object(issuer.kus,'patchTemplate') as render:
            for resource in ({}, {'status':{'conditions':[{'type':'Ready','status':'False'}]}}):
                with patch.object(issuer.kube,'kubectl',return_value=json.dumps(resource)), self.assertRaises(ValueError):issuer.create()
            with patch.object(issuer.kube,'kubectl',side_effect=RuntimeError('API denied')),self.assertRaises(RuntimeError):issuer.create()
            render.assert_not_called()

    def test_all_ingress_builders_receive_the_selected_name(self):
        with self.settings(),patch.object(util,'get_controller_http_timeout',return_value='5min'), patch.object(util,'get_ingress_class',return_value='traefik'), patch.object(util,'get_traefik_middleware_api_version',return_value='traefik.io/v1alpha1'):
            data=ingress_data.IngressData('https://synthetic.example')
            self.assertTrue(data._data['tls'])
            self.assertEqual(data._data['issuer_name'],'company-ca')
            data._data.update(ingress_name='synthetic',namespace='synthetic',secret_name='synthetic-tls',service_name='synthetic',service_port=8080,context_path='/')
            rendered=yaml.safe_load(expand_template('generic-ingress-tpl.yaml',data._data))
            self.assertEqual(rendered['metadata']['annotations']['cert-manager.io/cluster-issuer'],'company-ca')
            self.assertEqual(rendered['spec']['tls'][0]['secretName'],'synthetic-tls')

    def test_managed_acme_template_uses_the_selected_name(self):
        rendered=yaml.safe_load(expand_template('cluster-issuer.yaml',{'issuer_name':'letsencrypt-issuer','runtime':'k3s','issuer_class':'traefik','acme_server_url':'https://acme.example/directory','acme_registered_email':'synthetic@example.com'}))
        self.assertEqual(rendered['metadata']['name'],'letsencrypt-issuer')
        self.assertIn('acme',rendered['spec'])

    def test_legacy_default_and_unsafe_issuer_names(self):
        with self.settings(external=False,name=None):self.assertEqual(util.tls_issuer_name(),'letsencrypt-issuer')
        for name in ('invalid/name','Uppercase','x\n','x'*64):
            with self.settings(name=name),self.assertRaises(ValueError):util.tls_issuer_name()


if __name__=='__main__':unittest.main()
