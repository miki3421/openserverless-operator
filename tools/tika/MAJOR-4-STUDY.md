# Tika 4.2 compatibility study

This is an isolated AMD64 candidate, not a replacement for the current ADV
Tika 3.3.3 service. No deployment manifest or task catalog selects it.

## Result

The existing 17-contract fixture passes on the patched 3.3.3 image. On official
4.2.0, eight contracts pass and nine fail:

| Existing client contract | Observed 4.2.0 result | Explicit client change tested |
| --- | --- | --- |
| `X-TIKA:content` in six document metadata cases | HTTP 200, key absent | Read `tk:content` |
| `PUT /detect/stream` | HTTP 404 | Use `/detect` |
| `PUT /language/stream` | HTTP 404 | Use `/language` |
| `PUT /tika` with `Accept: text/html` | HTTP 406 | Use `/tika/html` |

Text extraction for plain text, HTML, PDF, DOCX, RTF and synthetic PNG OCR
works. Four concurrent requests retain their own content. With the explicit
4.x client changes, all 17 checks pass on both official and hardened 4.2.0.
These native checks do not turn the nine backwards-compatibility failures
into successes. `/unpack` also changes the ZIP layout: the original fixture
file becomes a package with `datapackage.json`, `rmeta.json` and
`unpacked/1.bin`. Empty text returns 422 on both tested images.

The original strict `tests/tika_compat.py` remains unchanged and fails at
`plain_metadata` on 4.2.0. `tests/tika_major_contracts.py` records all independent
observations and returns nonzero for a failed selected contract:

```sh
python3 -B tests/tika_major_contracts.py --image apache/tika:4.2.0-1-full --version 4.2.0
python3 -B tests/tika_major_contracts.py --image localhost/ops-advanced-tika:4.2.0-20261106-study-r1 --version 4.2.0 --contract native4
```

The fixture uses synthetic data, an owned internal Docker network, no published
ports, a read-only container, no host mounts, bounded CPU/memory/processes and
temporary storage. It never pulls an image and removes its resources.

## Security comparison

The November 6 Trivy database finds one Critical and three High Java matches
in the existing patched 3.3.3 image. The 4.2.0 distribution contains CXF 4.2.4
and Jetty 12.1.14 and has no Java matches in this scan. This is scanner evidence,
not proof of absence of vulnerabilities.

Official 4.2.0 has six High package matches in Ubuntu apt and an unused Pebble
binary. `Dockerfile.4-study` pins the upstream image, updates apt and removes
only that hash-bound unused helper. All 162 files under `/opt/tika-server`,
the original Java entrypoint and the non-root user are unchanged.

After this change the image has no Critical or High matches, but still has
640 Medium and 44 Low package matches (187 and 24 distinct vulnerability IDs).
The patched 3.3.3 comparison has 322 Medium and 41 Low package matches. The
4.x OS package inventory is larger (492 versus 361); the major update must
not be described as universally reducing every risk category.

Qualified local candidate:
`localhost/ops-advanced-tika:4.2.0-20261106-study-r1` at
`sha256:dda5f0a1e412dfdf5cfb9ddb8153001623b32276fb0842658b83bdf3f2eeb9db`.
Java file-manifest SHA256:
`a322ba378e249dc4e8da0ccd8d542c464cdcaaf0b782a5ce1efa7712ae902762`.

## Next qualification gate

Offer 4.x through an explicitly selected service/profile or migrate consumers
after enumerating their endpoint, metadata and configuration dependencies.
Do not silently switch the existing endpoint. Tika's legacy-key migration
filter is a possible partial adapter, but has not been tested here and would
not by itself restore removed HTTP paths or the unpack layout.

Before deployment, qualify Kubernetes resources, platform network controls,
client libraries, real application document fixtures, concurrency/limits and
rollback. ARM64, XML-to-JSON configuration conversion, external parser plugins,
native service binaries and application-level acceptance are not qualified.
The existing Tika 3.x Java findings remain open on ADV.

Upstream references (checked November 6, 2026):
- <https://tika.apache.org/docs/4.2.x/migration-to-4x/migrating-to-4x.html>
- <https://tika.apache.org/docs/4.2.x/using-tika/server/index.html>
- <https://tika.apache.org/docs/4.2.x/faq.html>

The documentation itself warns that portions require human review. The
compatibility statements above are based on the recorded requests, not solely
on those documents. Raw scans and failed/intermediate attempts are retained
outside the repository.
