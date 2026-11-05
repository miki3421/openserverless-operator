# Advanced volume exporter refresh

Both the Kafka source template and deploy archive input select the qualified
local volume-exporter20261105-r2 image. The exporter runs as65534, without Linux
capabilities or privilege escalation and with a read-only root and Kafka mount.
Kafka's own image, settings and writable PVC are unchanged.

The operator overlay is based on the previously qualified aligned20261105-r2,
replacing only the Kafka template and deploy archive. Existing messaging resume
reconciliation rolls Kafka to this exporter. On the Advanced lab, Kafka2/2 readiness,
all4 volume metrics, Alpha/Beta12 checks and streaming2 checks pass. Retain old
image and spec for rollback. No database upgrade or baseline change occurred.
Local image localhost/ops-advanced-security-aligned:20261105-r3,
manifest sha256:b2bba73f4e026f6b45937613b9d5f39c29ebe9a62643e18ef6f1394ec51c9e68.
Images are local qualification artifacts; no remote publication is implied.
