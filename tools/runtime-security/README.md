# Go 1.26 and Java 8 candidate runtimes

These recipes retain the OPS SDK init/run contract. They are qualification
candidates, not evidence of an accepted release.

Build `Dockerfile.proxy` first, then pass its immutable image reference as
`PROXY_IMAGE` to the Go and Java Dockerfiles. The proxy source tarball is pinned
by commit and checksum; its build runs the common SDK test suite. Base images
and the SQLite security carrier are immutable references. APT package versions
are recorded by the resulting image SBOM; repository updates can change a
later rebuild, which must receive a new identity and qualification.

Go preserves the original compiler and warms its standard-library cache under
the same `GOCACHE=/tmp` setting used by action compilation. The SQLite carrier
retains the qualified source backport and license evidence.

Java retains OpenJ9 and Java 8 while moving to Semeru 8u512 on Ubuntu 24.04.
`Launcher.java` comes from the pinned SDK commit with the `-exit` argument
compared by value. The old reference comparison could open an unintended file
descriptor during compiler preflight and truncate `rt.jar`. The rebuilt
launcher exits before opening the action result descriptor. `java-compile.py`
uses compiler exit status and retains successful diagnostics in the compiler
log: the proxy regards any compiler stdout/stderr as an initialization error.
Compilation and preflight failures return a nonzero exit status.

Run `tests/test_java_compiler.py`, then `protocol_probe.py` with immutable Go
and Java images, a JAR compiled with the previous Java runtime, a binary ZIP
from the previous Go runtime, and a private report path. The probe covers fresh
256 MiB containers, cold/warm execution, Unicode, source/archives, rejected
invalid source, the shipped SQLite ABI and JDK integrity. This local probe
does not establish Engine runtime selection or tenant isolation; those require
separate authenticated actions through the actual Controller/Invoker.
