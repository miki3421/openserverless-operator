# ADV operator fixture reconciliation

The complete isolated Python suite on the qualified retention operator image
initially ran 188 tests: 178 passed, two failed, seven errored and one was
skipped because the sibling task catalog was not mounted at the expected path.
The same nine failures had already been recorded before retention was added.

This change updates tests only. Milvus configuration moved from a ConfigMap to
a Secret in the datastore hardening work. The old fixtures passed plaintext
YAML as if it were an encoded Kubernetes Secret, omitted the legacy ConfigMap
fallback lookup, and expected a ConfigMap in rendered resources. They now use
`stringData` for desired objects and base64 `data` for API-returned Secrets.
The legacy fallback is still covered. Engine, WAL, metadata, volume and orphaned
state refusals remain asserted; a missing configuration is explicitly rejected.
Render checks assert that credentials are not put in a ConfigMap.

The FerretDB test supplied a nonexistent `/tmp/synthetic.sql` path to the real
PostgreSQL wrapper, which now protects rendered files with mode 0600. The test
now creates owned temporary fixtures and verifies those permissions while
retaining the real wrapper, primary selection, namespace and database checks.

With the task sibling mounted, the final suite ran **190 tests, all passed,
zero skipped**. The two additional cases cover legacy configuration fallback
and missing-configuration refusal. No production module or deployed image was
changed for these fixture corrections. This result is limited to this Python
unit suite on AMD64; it does not supersede other suites' recorded failures or
qualify a fresh installation, ARM64 or production upgrade.

Reproduction uses the existing qualified image, no network and a bounded
writable copy for tests that render manifests:

```sh
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 1g --pids-limit 256 \
  --tmpfs /tmp:rw,exec,nosuid,size=256m \
  -v "$PWD:/src:ro" -v "$TASK_SOURCE:/tasks:ro" \
  --entrypoint /bin/sh localhost/ops-advanced-operator:20261106-retention-r1 \
  -c 'cp -a /src /tmp/operator && ln -s /tasks /tmp/oplugins && cd /tmp/operator && /home/openserverless/.venv/bin/python -B -m unittest discover -s tests -p "test_*.py" -v'
```

The original failing log and final passing log are retained outside Git in the
qualification evidence directory. No baseline operation was performed.
