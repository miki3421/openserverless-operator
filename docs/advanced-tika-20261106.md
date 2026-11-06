# ADV Tika 3.3.3 qualification

Tika's classic 3.x parsing APIs remain available while moving the lab from the
3.2.3 image on end-of-life Ubuntu 25.04 to 3.3.3 on Ubuntu 26.04 LTS.
The image runs as the upstream UID/GID 35002 and retains its Java entrypoint.

`tools/tika/Dockerfile` pins upstream index
`sha256:6ef30abd65df4b6c478907904099d0245bdb0e0686f94fb9bc7493558442bb74`,
updates apt/libapt to 3.2.1ubuntu0.1 and libbrotli1 to 1.2.0-3ubuntu0.1, and
removes the unowned, unused `/usr/bin/pebble` base-image helper after verifying
its original hash. Tika starts Java directly; no parser or OCR executable is
removed. The upstream Tika server JAR remains byte-identical, SHA256
`4559c1fa6ca08390a800036438607e31a6763aba0ea11b928c3fce72315ecf0f`.

Build from the repository root:

```sh
docker build --pull=false -f tools/tika/Dockerfile -t localhost/ops-advanced-tika:3.3.3-20261106-r1 tools/tika
```

Qualified AMD64 image manifest:
`sha256:0e4becdc5a464242850047c7f645bfe156aa07e4b2756b3adfc0834470f36a21`.
Image configuration:
`sha256:e8b77072833661d9ff8fd442820e4780419d18f5f53848d8b500167746586f03`.
This is a local lab image, not a published multi-architecture release.

## Tests

`tests/tika_compat.py` uses only synthetic documents and owned internal Docker
networks, or an explicitly supplied loopback port-forward. Its 17 checks cover
plain text, HTML, PDF, DOCX, RTF, PNG OCR, metadata extraction, legacy detection
and language endpoints, HTML content negotiation and four concurrent requests.

- Old image: 17 PASS; candidate: 17 PASS.
- Separate K3s 1.36.4 lab: 17 PASS using the task's manifests, followed by pod
  replacement and preservation of the synthetic PVC. The owned namespace was
  removed. The first attempt raced pod creation after deletion; the corrected
  harness waits for creation before readiness. This wait is also added to the
  registry Kubernetes fixture. The earlier failure is retained.
- ADV before and after: 17 PASS each. Only the Tika image and task catalogue
  entry change. StatefulSet UID/spec comparison and all PVC/PV, service,
  network-policy and Tika ConfigMap comparisons pass.
- Alpha/Beta after the update: 12 application/security checks and 2 streaming
  checks PASS.

Earlier incomplete RTF input and a fixture function shadowing the Python
`concurrent` module were corrected; their failed runs remain in the evidence.
No failed result is counted as a passing run.

## Remaining limits

Same Trivy database, no ignore rules: old image 1 Critical / 24 High / 76 Medium /
16 Low; official 3.3.3 1 Critical / 10 High / 326 Medium / 46 Low; qualified image
1 Critical / 3 High / 317 Medium / 45 Low. Different OS releases and the old
image's end-of-life advisory coverage limit direct count comparisons.

Jetty 11.0.26 and CXF 4.0.11 findings remain unresolved in this image. In
particular CVE-2026-10050, CVE-2026-2332, CVE-2026-49875 and CVE-2026-50645 are
not declared fixed or dismissed. A compatible source backport or a separately
qualified Tika 4 migration is still required. The latter changes APIs, output
defaults, configuration and process behavior; it is not a tag-only replacement.

The 3.3 maintenance line also disables unsafe optional pipes/async/status
features by default. These were not enabled for compatibility. Custom server
configuration, extensions, external pipelines and ARM64 are not qualified by
these tests. The task's XML configuration mount and existing JVM configuration
are preserved; no previously inactive configuration is silently enabled.

References: https://tika.apache.org/ and
https://tika.apache.org/docs/4.2.x/migration-to-4x/migrating-tika-server-4x.html .
Private operational snapshots and full scanner/test evidence remain outside Git.
