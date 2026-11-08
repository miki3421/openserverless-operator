#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Local AMD64 package-refresh qualification using disposable synthetic data.

Never connects to Kubernetes. Requires Docker and existing local images; no pull.
The Perl regression is derived from Perl/perl5@03f74bb (GH #23388).
"""
import argparse
import base64
import json
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "docker.io/apache/couchdb:3.5.2@sha256:c703989c0a370a1a6b179785bcb5b2b5501347bb0219d02d597d0a9107eb8d28"
REGEX = r'''my $x=join "|", "aaa".."mzz"; my $y=join "|", "naa".."zzz";
my $r=qr/^(?:(?:$x)|(?:$y))$/;
for my $s ("aaa","mzz","naa","zzz") {die "positive failed\n" unless $s =~ $r;}
for my $s ("fnord","aa","zzzz","!") {die "negative failed\n" if $s =~ $r;}
print "eight regex controls PASS\n";'''


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], text=True, timeout=90, **kwargs)


def main(candidate):
    rows = []
    def passed(name):
        rows.append({"check": name, "status": "PASS"})
    def inspect(image, command):
        return docker("run", "--rm", "--pull=never", "--network", "none", "--memory", "256m", "--cpus", "1", "--entrypoint", "/bin/sh", image, "-ec", command)
    manifests = []
    for image in (BASE, candidate):
        packages = dict(line.split("\t") for line in inspect(image, "dpkg-query -W -f='${binary:Package}\\t${Version}\\n'").splitlines())
        payload = inspect(image, "find /opt/couchdb -type f -not -path '/opt/couchdb/data/*' -exec sha256sum {} + | sort; sha256sum /docker-entrypoint.sh /usr/bin/tini")
        manifests.append((packages, payload))
    changed = {p: [manifests[0][0].get(p), manifests[1][0].get(p)] for p in manifests[0][0].keys() | manifests[1][0].keys() if manifests[0][0].get(p) != manifests[1][0].get(p)}
    assert changed == {"libbrotli1:amd64": ["1.1.0-2+b7", "1.1.0-2+deb13u1"], "perl-base": ["5.40.1-6", "5.40.1-6+deb13u1"]}, changed
    passed("only_two_qualified_packages_changed")
    assert manifests[0][1] == manifests[1][1]
    passed("couchdb_erlang_entrypoint_tini_bytes_preserved")
    configs = [json.loads(docker("image", "inspect", image))[0]["Config"] for image in (BASE, candidate)]
    for key in ("Entrypoint", "Cmd", "Env", "User", "Volumes", "ExposedPorts", "WorkingDir", "Healthcheck"):
        assert configs[0].get(key) == configs[1].get(key), key
    passed("image_runtime_contract_preserved")
    for image, expected in ((BASE, False), (candidate, True)):
        result = subprocess.run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--memory", "256m", "--cpus", "1", "--entrypoint", "perl", image, "-e", REGEX], capture_output=True, timeout=30)
        assert (result.returncode == 0) is expected
    passed("perl_regex_regression_fails_before_passes_after")
    inspect(candidate, "perl -V:ptrsize | grep -F \"ptrsize='8'\"; ! perl -MArchive::Tar -e 1 2>/dev/null")
    passed("perl_64bit_and_archive_tar_absent")
    token = secrets.token_hex(6)
    network, volume, name = ("ops-couchdb-" + token + suffix for suffix in ("-net", "-data", "-server"))
    admin, passwords = secrets.token_hex(24), {u: secrets.token_hex(24) for u in ("alpha", "beta")}
    url = ""
    def request(path, method="GET", body=None, user="admin", expected=(200,)):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if user is not None:
            password = admin if user == "admin" else passwords.get(user, "invalid-synthetic")
            headers["Authorization"] = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
        req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as reply:
                status, result = reply.status, json.load(reply)
        except urllib.error.HTTPError as error:
            status, result = error.code, json.load(error)
        assert status in expected, (path, status)
        return result
    def start(image, env):
        nonlocal url
        docker("run", "-d", "--pull=never", "--name", name, "--network", network, "--memory", "1g", "--cpus", "2", "--env-file", str(env), "-v", volume + ":/opt/couchdb/data", "--entrypoint", "/bin/sh", image, "-ec",
               "printf '[couchdb]\\nsingle_node = true\\n[cluster]\\nn = 1\\n' > /opt/couchdb/etc/local.d/00-qualification.ini; exec tini -- /docker-entrypoint.sh /opt/couchdb/bin/couchdb")
        address = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Networks"][network]["IPAddress"]
        assert address
        url = "http://" + address + ":5984"
        for _ in range(60):
            try:
                request("/_up")
                return
            except (OSError, ValueError, AssertionError):
                time.sleep(1)
        raise RuntimeError("local CouchDB readiness timeout")
    try:
        docker("network", "create", "--internal", network)
        docker("volume", "create", volume)
        with tempfile.TemporaryDirectory(prefix="ops-couchdb-synthetic-") as directory:
            env = Path(directory) / "environment"
            env.write_text("COUCHDB_USER=admin\nCOUCHDB_PASSWORD=" + admin + "\n")
            env.chmod(0o600)
            start(BASE, env)
            assert request("/_cluster_setup")["state"] == "single_node_enabled"
            fixtures = {}
            for user in passwords:
                request("/_users/org.couchdb.user:" + user, "PUT", {"name": user, "password": passwords[user], "roles": [], "type": "user"}, expected=(201,))
                db = "/synthetic_" + user
                request(db, "PUT", expected=(201,))
                request(db + "/_security", "PUT", {"admins": {"names": [], "roles": []}, "members": {"names": [user], "roles": []}})
                doc = request(db + "/fixture", "PUT", {"marker": token, "tenant": user}, user=user, expected=(201,))
                fixtures[user] = doc["rev"]
                request(db + "/_design/qualification", "PUT", {"views": {"tenants": {"map": "function(doc) { if(doc.tenant) emit(doc.tenant, doc.marker); }"}}}, expected=(201,))
            passed("parent_seeded_two_authenticated_isolated_databases")
            docker("stop", "--time", "30", name)
            docker("rm", name)
            start(candidate, env)
            for user in passwords:
                db = "/synthetic_" + user
                doc = request(db + "/fixture", user=user)
                assert doc["_rev"] == fixtures[user] and doc["marker"] == token
                passed(user + "_existing_data_revision_preserved")
                assert request(db + "/_design/qualification/_view/tenants", user=user)["rows"][0]["key"] == user
                passed(user + "_existing_view_query")
                for other in (None, "invalid", "beta" if user == "alpha" else "alpha"):
                    request(db + "/fixture", user=other, expected=(401, 403))
                    request(db + "/forbidden", "PUT", {"marker": token}, user=other, expected=(401, 403))
                passed(user + "_anonymous_invalid_peer_read_write_denied")
                d = request(db + "/temporary", "PUT", {"marker": token}, user=user, expected=(201,))
                d = request(db + "/temporary", "PUT", {"_rev": d["rev"], "updated": True}, user=user, expected=(201,))
                request(db + "/temporary?rev=" + d["rev"], "DELETE", user=user)
                passed(user + "_legitimate_create_update_delete")
            docker("restart", "--time", "30", name)
            for _ in range(60):
                try:
                    assert request("/synthetic_alpha/fixture", user="alpha")["marker"] == token
                    assert request("/synthetic_beta/fixture", user="beta")["marker"] == token
                    passed("candidate_restart_persistence")
                    break
                except (OSError, ValueError, AssertionError):
                    time.sleep(1)
            else:
                raise RuntimeError("candidate restart failed")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
    print(json.dumps({"scope": "isolated local AMD64 synthetic data", "base": BASE, "candidate": candidate, "packageChanges": changed, "checks": rows, "platformExploitReproduced": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", help="already-built local candidate image")
    main(parser.parse_args().candidate)
