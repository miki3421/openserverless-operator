# Tika 3.3.3 HTTP dependency patch

The personal ADV server keeps Tika 3.3.3's application APIs and parser/OCR
payload. It rebuilds the two server modules against CXF 4.1.9 and Jetty 12.0.39,
rather than switching consumers to Tika 4. The strict 17-case Tika 3 fixture
remains unchanged.

## Source and build

Build from the operator repository root:

```sh
docker build --pull=false -f tools/tika/Dockerfile.http \
  -t miki3421/ops-advanced-tika:3.3.3-20261107-http-r3 .
```

- Tika source: `apache/tika` tag `3.3.3`, verified commit
  `f82f5d34ffab24069ca222fe4f8888a9d07f9062`.
- Maven builder: 3.9.11 / Temurin 21, index
  `sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237`.
- Existing hardened runtime base:
  `docker.io/miki3421/ops-advanced-tika:3.3.3-20261106-r1`, manifest
  `sha256:0e4becdc5a464242850047c7f645bfe156aa07e4b2756b3adfc0834470f36a21`.
- CXF source jar 4.1.9: SHA256
  `72d95931ce314fb115777273e063616ba468c5a1b85e05dc26eae6ada4f17199`.
  The selected `AttachmentDeserializerUtil.java` is also verified by digest.
- Qualified AMD64 runtime image:
  `docker.io/miki3421/ops-advanced-tika:3.3.3-20261107-http-r3`, manifest
  `sha256:af67ed502767ed03f350fc85f324c449132c1203aacc1639a317648c4894d8b6`.
- Final server jar: SHA256
  `559ff4b7db6285b8922181e0bd361d2e89c451dc3617dfe0ce09d2f3bbf7ab21`.

Maven uses strict checksums and a fixed archive timestamp. The image recipe
checks the source revision, verifies and applies `http-errors.patch`, and builds
the complete HTTP dependency closure. Seven CXF and seven Jetty modules must
have coherent versions. All 1,293 original Tika classes outside the server
package are checked byte-for-byte. XMLSchema 2.3.3 and Servlet 6.0 are coordinated
HTTP-stack dependencies; this is not an arbitrary replacement of shaded jars.

The existing runtime base, JRE, non-root uid/gid 35002, environment, entrypoint,
command, working directory, ports and volume declarations remain unchanged.
Only the server jar and identifying OCI labels change. The newer HTTP stack
requires Java 17 or later; the supplied runtime already satisfies this. This
does not qualify distributing the modified jar to an older Java 11 host.
The upstream legal resources remain in the rebuilt jar. This is a custom
personal build, not an official Apache Tika binary.

## Narrow compatibility patch

The dependency-only candidate passed normal requests but returned HTTP 500 for
some newly rejected malformed input. Its failing tests are retained separately.

`http-errors.patch` makes two narrow changes:

1. The existing Tika parse-exception mapper recognizes typed Jetty 400/413
   transport causes. Traversal is bounded and stops at explicit application/auth
   responses or an outer HTTP error. Unrelated I/O remains 500, ordinary parser
   errors remain 422, and direct/nested authentication responses remain 401.
2. Three existing CXF header-count rejection branches throw the already-supported
   `HeaderSizeExceededException`, so CXF returns 413. The count/size checks,
   parsing and rejection thresholds are preserved. No input limit is increased.

The error patch is applied to exact upstream sources before compilation. The
build executes eight classification controls, including benign server/parser
and authentication errors; it does not classify every exception as client input.

## Verification

- Original image: strict 17/17 parsing, metadata, legacy endpoint, OCR and
  concurrent-request contracts PASS. Four bounded malformed-input controls
  were accepted with 200, demonstrating the relevant parser/limit failures.
- Final image: strict 17/17 contracts and 11/11 HTTP security/legitimate controls
  PASS. They include both valid quoted chunk forms, two malformed variants,
  multipart positive/folded controls, distinct and repeated-empty header counts,
  folded aggregate size, and service usability afterwards. Inputs stay below
  16 KiB and are not load or crash probes.
- ADV: 17/17 before, 17/17 after, 11/11 HTTP checks after, and 17/17 after
  recreating the pod. StatefulSet identity/spec (apart from image), PVCs,
  Service, ConfigMap and NetworkPolicies are preserved. An owned synthetic file
  survives replacement and is removed after verification.
- Alpha/Beta after replacement: 12 application/auth, 10 direct backend read
  isolation, 24 streaming and two scheduled-heartbeat checks PASS. Both HTML/JS
  frontends and all 30 persistent workload controllers pass. Cron is verified
  through completed dispatch/Redis-heartbeat correlation; activation history
  remains unavailable. This is not an activation-storage or load qualification.

The full-image fixed-database Trivy comparison has no ignores:

| Image | Critical | High | Medium | Low |
| --- | ---: | ---: | ---: | ---: |
| Previous 3.3.3 ADV | 1 | 3 | 322 | 41 |
| HTTP patch | 0 | 0 | 320 | 40 |

Database SHA256:
`112895ef5c65cfe71007fc6d1ba8df0b6652412cd5c869f95e3d4723b4912508`,
updated 2026-11-06. Counts describe this image/database, not every ADV component.

## Limits

The HTTP fixture reproduces malformed-chunk and attachment-header acceptance on
the original image and verifies rejection on the candidate. It does not prove
an Internet exploit or schema-XXE/Digest-auth attack through this exact REST
configuration. Those two dependency corrections rely on upstream fixed versions
and source controls. Custom extensions, TLS variants, external pipelines,
optional unsafe features, ARM64, load/HA and production remain unqualified.
The baseline is unchanged. No whole-suite or independent-review PASS is claimed.
Private operational snapshots and raw evidence stay outside Git.

Primary references:
- https://cxf.apache.org/docs/41-migration-guide.html
- https://cxf.apache.org/security-advisories.data/CVE-2026-49875.txt
- https://cxf.apache.org/security-advisories.data/CVE-2026-50645.txt
- https://github.com/jetty/jetty.project/security/advisories/GHSA-355h-qmc2-wpwf
- https://github.com/jetty/jetty.project/security/advisories/GHSA-2fvj-hgj9-j2gr
