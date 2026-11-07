# ADV registry release layer

The recipe below builds the registry-relocation parent. The final fresh-install
candidate also includes the compatible ZooKeeper layer; use the current pins in
`release-images.json` and
[`docs/advanced-fresh-dockerhub-20261107.md`](../../docs/advanced-fresh-dockerhub-20261107.md)
for the selected image and observed qualification, rather than installing the
older parent tag from this build example.

The Dockerfile relocates qualified AMD64 component references to the personal
Docker Hub namespace `miki3421`. Build it from the operator repository root:

```sh
docker build --pull=false -f tools/registry-release/Dockerfile \
  -t miki3421/ops-advanced-operator:20261107-dockerhub-r1 .
```

The pinned parent is the qualified fresh-install candidate with Prometheus
3.16.0, Alertmanager 0.34.2 and kube-state-metrics 2.20.0. Its 148-member
deployment archive is verified before changing five manifests. Every change
must be the exact registry-prefix replacement; all other member contents and
metadata are retained. The two monitoring profile files and Kafka template
use the same destination references. Binary/library payloads are unchanged.

Installations must use the matching task catalog and bootstrap fixes: the
operator pod passes its selected image reference to initialization, scheduler,
quota and retention jobs, and its namespaced Role includes NetworkPolicy
reconciliation. A locally cached older operator image is not an installation
dependency. The volume-exporter sidecar is an explicit release input.

This is an incremental software packaging recipe. A registry push or image
import does not establish successful fresh-cluster installation. Final remote
digests, anonymous pull checks, clean-install results and application tests
belong to the qualification evidence. ARM64 and production remain separate.

Credentials and operational logs must stay outside this repository. Public
vendor images retain their qualified upstream references.
