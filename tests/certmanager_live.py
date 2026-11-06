# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Explicit synthetic certificate qualification; creates/deletes one owned namespace.

No ACME account or public certificate is used. Keys stay in API memory and are
never printed or written to the report. Requires kubectl and cryptography.
"""
import argparse
import base64
import datetime
import json
import subprocess
import time
import uuid
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", required=True)
    args = parser.parse_args()
    ns = "ops-cert-test-" + uuid.uuid4().hex[:12]
    checks = []
    uid = None

    def ctl(*words, obj=None, fail=True):
        result = subprocess.run([args.kubectl, "--kubeconfig", args.kubeconfig,
                                 "--request-timeout=30s", *words],
                                input=json.dumps(obj) if obj is not None else None,
                                text=True, capture_output=True, timeout=150)
        if fail and result.returncode:
            # Avoid reflecting request content, including synthetic keys, in logs.
            raise RuntimeError(f"kubectl {words[0]} failed (exit {result.returncode})")
        return result

    def get(kind, name):
        return json.loads(ctl("get", kind, name, "-n", ns, "-o", "json").stdout)

    def apply(kind, name, spec):
        return ctl("apply", "-f", "-", obj={"apiVersion": "cert-manager.io/v1", "kind": kind,
                   "metadata": {"name": name, "namespace": ns}, "spec": spec})

    def ready(kind, name):
        ctl("wait", f"{kind}/{name}", "-n", ns, "--for=condition=Ready", "--timeout=120s")

    def passed(name):
        checks.append({"check": name, "status": "PASS"})
        print(json.dumps(checks[-1]), flush=True)

    def material():
        data = get("secret", "leaf-tls")["data"]
        cert = x509.load_pem_x509_certificate(base64.b64decode(data["tls.crt"]))
        key = serialization.load_pem_private_key(base64.b64decode(data["tls.key"]), password=None)
        if cert.public_key().public_numbers() != key.public_key().public_numbers():
            raise RuntimeError("Certificate/key mismatch")
        return cert, key

    try:
        created = json.loads(ctl("create", "namespace", ns, "-o", "json").stdout)
        uid = created["metadata"]["uid"]
        apply("Issuer", "selfsigned", {"selfSigned": {}}); ready("issuer", "selfsigned")
        passed("self_signed_issuer")
        apply("Certificate", "root", {"secretName": "root-ca", "isCA": True,
              "commonName": "OPS synthetic CA", "duration": "48h", "renewBefore": "12h",
              "privateKey": {"algorithm": "RSA", "size": 2048},
              "issuerRef": {"name": "selfsigned"}})
        ready("certificate", "root"); passed("root_ca_issuance")
        apply("Issuer", "ca", {"ca": {"secretName": "root-ca"}}); ready("issuer", "ca")
        passed("ca_issuer")
        apply("Certificate", "leaf", {"secretName": "leaf-tls", "dnsNames": ["synthetic.ops.invalid"],
              "duration": "24h", "renewBefore": "6h", "privateKey": {"algorithm": "RSA", "size": 2048,
              "rotationPolicy": "Always"}, "issuerRef": {"name": "ca"}})
        ready("certificate", "leaf")
        cert, key = material(); passed("leaf_certificate_key_match")
        root = x509.load_pem_x509_certificate(base64.b64decode(get("secret", "root-ca")["data"]["tls.crt"]))
        root.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
        passed("leaf_chain_signature")
        if cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) != ["synthetic.ops.invalid"]:
            raise RuntimeError("Unexpected SAN")
        passed("leaf_dns_san")
        before = get("certificate", "leaf")
        revision = before["status"]["revision"]
        # Same condition as cmctl renew: retain other status and resourceVersion.
        conditions = [c for c in before["status"]["conditions"] if c["type"] != "Issuing"]
        conditions.append({"type": "Issuing", "status": "True", "reason": "ManuallyTriggered",
                           "message": "Synthetic qualification renewal", "observedGeneration": before["metadata"]["generation"],
                           "lastTransitionTime": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")})
        ctl("patch", "certificate", "leaf", "-n", ns, "--subresource=status", "--type=merge", "--patch-file=/dev/stdin",
            obj={"metadata": {"resourceVersion": before["metadata"]["resourceVersion"]}, "status": {"conditions": conditions}})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = get("certificate", "leaf")
            if current.get("status", {}).get("revision", 0) > revision: break
            time.sleep(1)
        else: raise RuntimeError("Renewal timed out")
        ready("certificate", "leaf")
        after, rotated = material()
        if after.serial_number == cert.serial_number: raise RuntimeError("Certificate did not renew")
        passed("manual_renewal_new_serial")
        if rotated.public_key().public_numbers() == key.public_key().public_numbers(): raise RuntimeError("Private key did not rotate")
        passed("renewal_private_key_rotation")
        invalid = {"apiVersion": "cert-manager.io/v1", "kind": "Certificate", "metadata": {"name": "invalid", "namespace": ns},
                   "spec": {"secretName": "invalid", "duration": "1s", "dnsNames": ["synthetic.ops.invalid"], "issuerRef": {"name": "ca"}}}
        denial = ctl("apply", "-f", "-", obj=invalid, fail=False)
        if denial.returncode == 0 or "duration" not in denial.stderr:
            raise RuntimeError("Invalid certificate was not refused by admission")
        passed("invalid_duration_admission_denial")
        # Roll only this synthetic installation's cert-manager in the isolated lab.
        for deployment in ("cert-manager", "cert-manager-cainjector", "cert-manager-webhook"):
            ctl("rollout", "restart", "deployment/" + deployment, "-n", "cert-manager")
            ctl("rollout", "status", "deployment/" + deployment, "-n", "cert-manager", "--timeout=120s")
        stable, _ = material()
        if stable.serial_number != after.serial_number: raise RuntimeError("Unexpected reissue on restart")
        passed("controller_webhook_restart_preserves_certificate")
    finally:
        if uid:
            current = json.loads(ctl("get", "namespace", ns, "-o", "json").stdout)
            if current["metadata"]["uid"] != uid: raise RuntimeError("Cleanup UID mismatch")
            ctl("delete", "namespace", ns, "--wait=true", "--timeout=120s")
            passed("owned_namespace_cleanup")
    print(json.dumps({"checks": checks, "syntheticOnly": True, "externalAcmeQualified": False}))


if __name__ == "__main__": main()
