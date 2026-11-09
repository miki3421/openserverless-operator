#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Isolated exact-image PostgreSQL/libxml2 checks; never connects to a cluster."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import uuid

from libxml2_source_guard_contract import (
    INSTALLED_LIBRARY_SHA256,
    PATCHED_SHA256,
    PATCH_SHA256,
    RUNTIME_DEB_SHA256,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "openserverless/files/postgres-profile.json"
EXPECTED_POC_SHA256 = "1e1b97af5d7c617b2b33f65cf44e3154daef357cf261b82fe7012ac438a65439"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          timeout=kwargs.pop("timeout", 90), **kwargs)


def inside(image, command):
    return docker("run", "--rm", "--pull=never", "--network", "none",
                  "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                  "--entrypoint", "/bin/sh", image, "-ec", command).stdout.decode()


def start_postgres(image, name, data_directory, socket_directory):
    docker("run", "-d", "--name", name, "--pull=never", "--network", "none",
           "--read-only", "--user", "postgres", "--cap-drop=ALL",
           "--security-opt=no-new-privileges", "--memory", "1g", "--cpus", "1",
           "--pids-limit", "128", "--ulimit", "core=0",
           "--mount", "type=bind,source=" + str(data_directory) + ",target=/var/lib/postgresql",
           "--mount", "type=bind,source=" + str(socket_directory) + ",target=/var/run/postgresql",
           "--tmpfs", "/tmp:rw,uid=999,gid=999,size=32m",
           "-e", "POSTGRES_HOST_AUTH_METHOD=trust", image, "postgres",
           "-c", "listen_addresses=")
    for _ in range(100):
        try:
            startup = docker("logs", name)
            initialized = b"init process complete; ready for start up" in startup.stdout
            existing = b"Skipping initialization" in startup.stdout
            if not (initialized or existing):
                time.sleep(0.2)
                continue
            pg = PG(socket_directory)
            if not pg.query("SELECT NOT pg_is_in_recovery()")["errors"]:
                return pg
            pg.sock.close()
        except (OSError, ConnectionError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Isolated PostgreSQL readiness failed")


class PG:
    """Minimal local trust-auth PG wire client, including binary XML receive."""
    def __init__(self, directory, database="postgres"):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(15)
        # Managed artifact roots can exceed Linux's AF_UNIX pathname limit.
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.sock.connect("/proc/self/fd/" + str(fd) + "/.s.PGSQL.5432")
        finally:
            os.close(fd)
        payload = (struct.pack("!I", 196608) + b"user\0postgres\0database\0"
                   + database.encode() + b"\0\0")
        self.sock.sendall(struct.pack("!I", len(payload) + 4) + payload)
        self.drain()

    def read(self, count):
        out = b""
        while len(out) < count:
            part = self.sock.recv(count - len(out))
            if not part:
                raise ConnectionError("PostgreSQL backend closed connection")
            out += part
        return out

    def send(self, kind, payload):
        self.sock.sendall(kind + struct.pack("!I", len(payload) + 4) + payload)

    def drain(self):
        rows, errors = [], []
        while True:
            kind = self.read(1)
            length = struct.unpack("!I", self.read(4))[0]
            data = self.read(length - 4)
            if kind == b"R" and data != struct.pack("!I", 0):
                raise RuntimeError("Isolated instance unexpectedly requires authentication")
            if kind == b"D":
                count = struct.unpack_from("!H", data)[0]
                row, pos = [], 2
                for _ in range(count):
                    n = struct.unpack_from("!i", data, pos)[0]
                    pos += 4
                    row.append(None if n == -1 else data[pos:pos+n].decode(errors="replace"))
                    if n >= 0:
                        pos += n
                rows.append(row)
            if kind == b"E":
                fields = {x[:1].decode(): x[1:].decode(errors="replace")
                          for x in data.split(b"\0") if x}
                errors.append({k: fields.get(k) for k in ("C", "M")})
            if kind == b"Z":
                return {"rows": rows, "errors": errors}

    def query(self, sql):
        self.send(b"Q", sql.encode() + b"\0")
        return self.drain()

    def binary_xml(self, data):
        self.send(b"P", b"\0SELECT $1 IS NOT NULL\0" + struct.pack("!HI", 1, 142))
        self.send(b"B", b"\0\0" + struct.pack("!HHHi", 1, 1, 1, len(data))
                  + data + struct.pack("!H", 0))
        self.send(b"E", b"\0" + struct.pack("!I", 0))
        self.send(b"S", b"")
        return self.drain()


CASES = {
    "document": "SELECT XMLPARSE(DOCUMENT '<r><v>ADV</v></r>') IS NOT NULL",
    "content": "SELECT XMLPARSE(CONTENT 'text<a/>tail') IS NOT NULL",
    "whitespace": "SELECT XMLPARSE(DOCUMENT '<r>  <v/>  </r>' PRESERVE WHITESPACE)::text = '<r>  <v/>  </r>'",
    "unicode": "SELECT (xpath('string(/r)', XMLPARSE(DOCUMENT '<r>caffè 日本</r>')))[1]::text = 'caffè 日本'",
    "general_entity": "SELECT (xpath('string(/r)', XMLPARSE(DOCUMENT '<!DOCTYPE r [<!ENTITY a \"ADV\">]><r>&a;</r>')))[1]::text = 'ADV'",
    "parameter_entity": "SELECT XMLPARSE(DOCUMENT '<!DOCTYPE r [<!ENTITY % spec \"<!ELEMENT r (#PCDATA)>\">%spec;]><r>ok</r>') IS NOT NULL",
    # PostgreSQL validates DTD defaults while parsing XMLPARSE(DOCUMENT), but
    # stores the original lexical value.  A later xpath() reparses that value
    # without XML_PARSE_DTDATTR, so expecting the defaulted attribute there is
    # not a PostgreSQL contract.  The native libxml2 probe covers materializing
    # the default; this control checks that PostgreSQL still accepts it.
    "dtd_default": "SELECT XMLPARSE(DOCUMENT '<!DOCTYPE r [<!ATTLIST r mode CDATA \"ADV\">]><r/>') IS NOT NULL",
    "content_with_doctype": "SELECT XMLPARSE(CONTENT '<!DOCTYPE r [<!ENTITY a \"ok\">]><r>&a;</r>') IS NOT NULL",
    "xpath_namespace": "SELECT cardinality(xpath('/x:r/x:v', '<r xmlns=\"urn:adv\"><v/></r>'::xml, ARRAY[ARRAY['x','urn:adv']])) = 1",
    "xmltable_typed": "SELECT sum(n)=3 FROM XMLTABLE('/r/v' PASSING '<r><v>1</v><v>2</v></r>' COLUMNS n integer PATH '.')",
    "wellformed_true": "SELECT xml_is_well_formed_document('<r/>')",
    "wellformed_false": "SELECT NOT xml_is_well_formed_document('<r>')",
    "external_entity_blocked": "SELECT (xpath('string(/r)', XMLPARSE(DOCUMENT '<!DOCTYPE r [<!ENTITY a SYSTEM \"file:///etc/passwd\">]><r>&a;</r>')))[1]::text = ''",
    "pgvector": "CREATE EXTENSION IF NOT EXISTS vector; SELECT '[1,2,3]'::vector <-> '[1,2,3]'::vector = 0",
    "transaction": "BEGIN; CREATE TABLE compatibility_rows(id integer PRIMARY KEY, value text); INSERT INTO compatibility_rows VALUES(1,'kept'); COMMIT; SELECT value='kept' FROM compatibility_rows",
    "rollback": "BEGIN; UPDATE compatibility_rows SET value='discarded'; ROLLBACK; SELECT value='kept' FROM compatibility_rows",
    "tenant_setup": "CREATE ROLE tenant_alpha LOGIN; CREATE ROLE tenant_beta LOGIN; CREATE SCHEMA tenant_alpha AUTHORIZATION tenant_alpha; SET ROLE tenant_alpha; CREATE TABLE tenant_alpha.private_rows(id int); INSERT INTO tenant_alpha.private_rows VALUES(1); SELECT count(*)=1 FROM tenant_alpha.private_rows; RESET ROLE",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture-module", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    checks = []
    profile = json.loads(PROFILE_PATH.read_text())
    if len(profile.get("upgrade_from_images", [])) != 1:
        raise RuntimeError("PostgreSQL contract requires one exact predecessor")
    base = profile["upgrade_from_images"][0]
    candidate = profile["image"]

    def check(name, ok, detail=None):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        print(name + ": " + checks[-1]["status"], flush=True)

    before, after = [], []
    for image in (base, candidate):
        before_or_after = dict(x.split("\t", 1) for x in inside(image, "dpkg-query -W -f='${binary:Package}\\t${Version}\\n'").splitlines())
        (before if not before else after).append(before_or_after)
    a, b = before[0], after[0]
    delta = {k: [a.get(k), b.get(k)] for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    check("only_libxml2_changes", delta == {"libxml2:amd64": ["2.9.14+dfsg-1.3~deb12u6", "2.9.14+dfsg-1.3~deb12u6+0ops1"]}, delta)
    configs = [json.loads(docker("image", "inspect", x).stdout)[0]["Config"] for x in (base, candidate)]
    check("image_runtime_config_unchanged", configs[0] == configs[1])
    stable = "find /usr/lib/postgresql/18 /usr/share/postgresql/18 /usr/local/bin -type f -exec sha256sum {} + | sort"
    check("postgres_pgvector_entrypoints_byte_identical", inside(base, stable) == inside(candidate, stable))
    check("dpkg_audit", not inside(candidate, "dpkg --audit").strip())
    check("patch_receipt", "halt-state guards" in inside(candidate, "cat /usr/share/ops-security/libxml2/SOURCE"))
    provenance = inside(candidate,
        "cat /usr/share/ops-security/libxml2/patched-source.sha256; "
        "sha256sum /usr/share/ops-security/libxml2/libxml2-cve-2026-6653.patch; "
        "grep 'libxml2_2.9.14+dfsg-1.3~deb12u6+0ops1_amd64.deb' "
        "/usr/share/ops-security/libxml2/SHA256SUMS; "
        "sha256sum /usr/lib/x86_64-linux-gnu/libxml2.so.2.9.14").splitlines()
    check("candidate_binary_bound_to_qualified_source", provenance == [
        PATCHED_SHA256 + "  parser.c",
        PATCH_SHA256 + "  /usr/share/ops-security/libxml2/libxml2-cve-2026-6653.patch",
        RUNTIME_DEB_SHA256 + "  ./libxml2_2.9.14+dfsg-1.3~deb12u6+0ops1_amd64.deb",
        INSTALLED_LIBRARY_SHA256 + "  /usr/lib/x86_64-linux-gnu/libxml2.so.2.9.14",
    ], provenance)

    spec = importlib.util.spec_from_file_location("fixture", args.fixture_module)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    poc = base64.b64decode(fixture.POC, validate=True)
    check("public_fixture_hash",
          fixture.POC_SHA256 == EXPECTED_POC_SHA256
          and hashlib.sha256(poc).hexdigest() == EXPECTED_POC_SHA256)
    reports = {}
    for label, image in (("base", base), ("candidate", candidate)):
        name = "ops-pg-xml-" + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix="pgxml-", dir=args.scratch) as path:
            directory = Path(path)
            directory.chmod(0o777)
            data_directory = directory / "data"
            socket_directory = directory / "socket"
            data_directory.mkdir(mode=0o777)
            socket_directory.mkdir(mode=0o777)
            data_directory.chmod(0o777)
            socket_directory.chmod(0o777)
            try:
                pg = start_postgres(image, name, data_directory, socket_directory)
                rows = {}
                for case, sql in CASES.items():
                    reply = pg.query(sql)
                    rows[case] = reply
                    check(label + "_" + case, not reply["errors"] and reply["rows"][-1:] == [["t"]], reply)
                reply = pg.query("SET ROLE tenant_beta; SELECT * FROM tenant_alpha.private_rows")
                check(label + "_tenant_peer_denied", bool(reply["errors"]) and reply["errors"][0]["C"] == "42501", reply)
                pg.query("RESET ROLE")
                reply = pg.query("SELECT XMLPARSE(DOCUMENT '<r>')")
                check(label + "_invalid_xml_errors", bool(reply["errors"]), reply)
                check(label + "_connection_recovers", pg.query("SELECT 1")["rows"] == [["1"]])
                trigger = []
                for payload in (poc, poc.decode("utf8", errors="replace").encode("utf8"), b"<!DOCTYPE r [<!ENTITY % p SYSTEM 'file:///no-such-file'>%p;]><r/>"):
                    try:
                        reply = pg.binary_xml(payload)
                        alive = pg.query("SELECT 1")["rows"] == [["1"]]
                        trigger.append({"sha256": hashlib.sha256(payload).hexdigest(), "errors": reply["errors"], "connection_alive": alive})
                    except (ConnectionError, OSError) as exc:
                        trigger.append({"sha256": hashlib.sha256(payload).hexdigest(), "connection_alive": False, "exception": type(exc).__name__})
                        if label == "base":
                            break
                        raise
                if label == "candidate":
                    check("candidate_binary_xml_inputs_no_crash", len(trigger) == 3 and all(x["connection_alive"] for x in trigger), trigger)
                    check("candidate_exact_poc_rejected", bool(trigger[0]["errors"]), trigger[0])
                    dump = docker("exec", name, "pg_dumpall", "-U", "postgres", "-h", "/var/run/postgresql", "--no-role-passwords").stdout
                    check("candidate_logical_dump", b"compatibility_rows" in dump and b"CREATE EXTENSION IF NOT EXISTS vector" in dump)
                    database_dump = docker("exec", name, "pg_dump", "-U", "postgres",
                                           "-h", "/var/run/postgresql", "postgres").stdout
                    docker("exec", name, "createdb", "-U", "postgres",
                           "-h", "/var/run/postgresql", "restored")
                    docker("exec", "-i", name, "psql", "-v", "ON_ERROR_STOP=1",
                           "-U", "postgres", "-h", "/var/run/postgresql",
                           "-d", "restored", input=database_dump)
                    restored = docker("exec", name, "psql", "-At", "-U", "postgres",
                                      "-h", "/var/run/postgresql", "-d", "restored",
                                      "-c", "SELECT (SELECT value='kept' FROM compatibility_rows WHERE id=1) AND EXISTS (SELECT FROM pg_extension WHERE extname='vector') AND '[1,2,3]'::vector <-> '[1,2,3]'::vector = 0").stdout.strip()
                    check("candidate_logical_restore", restored == b"t", restored.decode(errors="replace"))
                    pg.sock.close()
                    docker("restart", name, timeout=120)
                    for _ in range(100):
                        try:
                            pg = PG(socket_directory)
                            if pg.query("SELECT value='kept' FROM compatibility_rows WHERE id=1")["rows"] == [["t"]]:
                                break
                            pg.sock.close()
                        except (OSError, ConnectionError):
                            pass
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("Candidate did not recover its isolated data after restart")
                    check("candidate_restart_persistence", True)
                reports[label] = {"controls": rows, "binary_xml": trigger}
                pg.sock.close()
            finally:
                logs = subprocess.run(["docker", "logs", name], capture_output=True, timeout=15)
                (args.scratch / (name + ".log")).write_bytes(logs.stdout + logs.stderr)
                subprocess.run(["docker", "rm", "-f", "-v", name], check=True, capture_output=True, timeout=30)

    # Exercise the authorized transition itself: the predecessor initializes
    # the physical data directory, then the candidate opens those exact files.
    # This is distinct from fresh-image smoke tests and catches layout/upgrade
    # incompatibilities before a Kubegres PVC is touched.
    transition = {}
    with tempfile.TemporaryDirectory(prefix="pgupgrade-", dir=args.scratch) as path:
        root = Path(path)
        data_directory = root / "data"
        socket_directory = root / "socket"
        data_directory.mkdir()
        socket_directory.mkdir()
        data_directory.chmod(0o777)
        socket_directory.chmod(0o777)
        active = []
        base_name = "ops-pg-upgrade-base-" + uuid.uuid4().hex[:8]
        candidate_name = "ops-pg-upgrade-new-" + uuid.uuid4().hex[:8]
        try:
            active.append(base_name)
            pg = start_postgres(base, base_name, data_directory, socket_directory)
            created = pg.query("CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE upgrade_marker(id integer PRIMARY KEY, payload text); INSERT INTO upgrade_marker VALUES(1,'from-base'); SELECT payload='from-base' FROM upgrade_marker WHERE id=1")
            check("predecessor_creates_transition_data",
                  not created["errors"] and created["rows"][-1:] == [["t"]], created)
            pg.sock.close()
            docker("stop", "-t", "30", base_name, timeout=60)
            logs = docker("logs", base_name)
            (args.scratch / (base_name + ".log")).write_bytes(logs.stdout + logs.stderr)
            docker("rm", "-v", base_name)
            active.remove(base_name)

            active.append(candidate_name)
            pg = start_postgres(candidate, candidate_name, data_directory, socket_directory)
            opened = pg.query("SELECT (SELECT payload='from-base' FROM upgrade_marker WHERE id=1) AND EXISTS (SELECT FROM pg_extension WHERE extname='vector') AND '[1,2,3]'::vector <-> '[1,2,3]'::vector = 0")
            check("candidate_opens_predecessor_data_and_pgvector",
                  not opened["errors"] and opened["rows"] == [["t"]], opened)
            rolled_back = pg.query("BEGIN; UPDATE upgrade_marker SET payload='discarded'; ROLLBACK; SELECT payload='from-base' FROM upgrade_marker WHERE id=1")
            check("candidate_rollback_on_predecessor_data",
                  not rolled_back["errors"] and rolled_back["rows"][-1:] == [["t"]], rolled_back)
            database_dump = docker("exec", candidate_name, "pg_dump", "-U", "postgres",
                                   "-h", "/var/run/postgresql", "postgres").stdout
            docker("exec", candidate_name, "createdb", "-U", "postgres",
                   "-h", "/var/run/postgresql", "upgrade_restored")
            docker("exec", "-i", candidate_name, "psql", "-v", "ON_ERROR_STOP=1",
                   "-U", "postgres", "-h", "/var/run/postgresql",
                   "-d", "upgrade_restored", input=database_dump)
            restored = docker("exec", candidate_name, "psql", "-At", "-U", "postgres",
                              "-h", "/var/run/postgresql", "-d", "upgrade_restored",
                              "-c", "SELECT payload='from-base' FROM upgrade_marker WHERE id=1").stdout.strip()
            check("transition_logical_dump_restore", restored == b"t",
                  restored.decode(errors="replace"))
            pg.sock.close()
            docker("restart", candidate_name, timeout=120)
            for _ in range(100):
                try:
                    pg = PG(socket_directory)
                    if pg.query("SELECT payload='from-base' FROM upgrade_marker WHERE id=1")["rows"] == [["t"]]:
                        break
                    pg.sock.close()
                except (OSError, ConnectionError):
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Candidate did not reopen predecessor data after restart")
            check("transition_restart_persistence", True)
            transition = {"base_container": base_name,
                          "candidate_container": candidate_name,
                          "data_directory_reused": True}
            pg.sock.close()
        finally:
            for container in active:
                logs = subprocess.run(["docker", "logs", container], capture_output=True,
                                      timeout=15)
                (args.scratch / (container + ".log")).write_bytes(logs.stdout + logs.stderr)
                subprocess.run(["docker", "rm", "-f", "-v", container],
                               check=False, capture_output=True, timeout=30)
    args.output.write_text(json.dumps({"base": base, "candidate": candidate,
        "package_delta": delta, "checks": checks, "sql": reports,
        "transition": transition}, indent=2) + "\n")
    raise SystemExit(0 if all(x["status"] == "PASS" for x in checks) else 1)


if __name__ == "__main__":
    main()
