# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Qualify projected-config reload/retry behavior in an owned internal network."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

SERVER = '''import http.server,json,pathlib
root=pathlib.Path('/data')
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  control=json.loads((root/'control.json').read_text())
  code=503 if control['fail'] else 200
  event={'method':'POST','path':self.path,'code':code,'config':(root/'config/prometheus.yml').read_text()}
  with (root/'events.jsonl').open('a') as f:f.write(json.dumps(event)+'\\n')
  self.send_response(code);self.end_headers();self.wfile.write(b'fixture')
 def log_message(self,*args):pass
http.server.HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    token = "ops-reloader-" + uuid.uuid4().hex[:10]
    network, server, client = token, token + "-server", token + "-client"
    owned = []

    def docker(*words):
        return subprocess.check_output(["docker", *words], text=True, stderr=subprocess.STDOUT).strip()

    with tempfile.TemporaryDirectory(prefix=token + "-") as directory:
        root = Path(directory); config = root / "config"; config.mkdir(mode=0o755)
        (root / "control.json").write_text('{"fail": false}')

        def project(generation):
            target = config / ("..v" + str(generation)); target.mkdir(mode=0o755)
            (target / "prometheus.yml").write_text(f"generation: {generation}\n")
            os.symlink(target.name, config / "..next")
            os.replace(config / "..next", config / "..data")
            if not (config / "prometheus.yml").exists(): os.symlink("..data/prometheus.yml", config / "prometheus.yml")

        def events():
            path = root / "events.jsonl"
            if not path.exists(): return []
            return [json.loads(line) for line in path.read_text().splitlines() if line]

        def wait(predicate, name):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if predicate(events()):
                    print(json.dumps({"check": name, "status": "PASS"}), flush=True)
                    return
                time.sleep(.1)
            raise RuntimeError(name + " timed out")

        project(1)
        try:
            docker("network", "create", "--internal", "--label", "ops.qualifier=" + token, network)
            owned.append(("network", network))
            docker("run", "-d", "--name", server, "--pull=never", "--network", network,
                   "--network-alias", "receiver", "--read-only", "--cap-drop=ALL",
                   "--security-opt=no-new-privileges", "--memory=128m", "--pids-limit=64", "--user=0",
                   "-v", f"{root}:/data", "--entrypoint=python",
                   "localhost/ops-advanced-operator:20261106-certmanager-r2", "-u", "-c", SERVER)
            owned.append(("container", server))
            docker("run", "-d", "--name", client, "--pull=never", "--network", network,
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--memory=128m", "--pids-limit=64", "-v", f"{config}:/etc/config:ro",
                   args.image, "--watched-dir=/etc/config", "--reload-url=http://receiver:9000/-/reload",
                   "--watch-interval=1s", "--delay-interval=100ms", "--retry-interval=200ms")
            owned.append(("container", client))
            wait(lambda es: any(e["code"] == 200 and e["config"] == "generation: 1\n" for e in es), "initial_reload")
            project(2)
            wait(lambda es: any(e["code"] == 200 and e["config"] == "generation: 2\n" for e in es), "projected_symlink_rotation")
            count = len(events()); time.sleep(2)
            if len(events()) != count: raise RuntimeError("Unchanged configuration reloaded unexpectedly")
            print(json.dumps({"check": "unchanged_config_no_reload", "status": "PASS"}), flush=True)
            (root / "control.json").write_text('{"fail": true}')
            project(3)
            wait(lambda es: sum(e["code"] == 503 for e in es) >= 2, "failed_endpoint_retried")
            (root / "control.json").write_text('{"fail": false}')
            wait(lambda es: any(e["code"] == 200 and e["config"] == "generation: 3\n" for e in es), "retry_recovers_latest_config")
            if docker("inspect", "-f", "{{.State.Running}}", client) != "true": raise RuntimeError("Reloader stopped")
            if any(e["method"] != "POST" or e["path"] != "/-/reload" for e in events()): raise RuntimeError("Unexpected reload request")
            print(json.dumps({"check": "process_and_request_contract", "status": "PASS"}), flush=True)
        finally:
            for kind, name in reversed(owned):
                if kind == "container": docker("rm", "-f", name)
                else: docker("network", "rm", name)
    print(json.dumps({"image": args.image, "passed": 6, "ownedResourcesRemoved": True}))


if __name__ == "__main__": main()
