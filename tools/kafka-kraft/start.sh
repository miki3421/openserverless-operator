#!/bin/sh
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
set -eu
umask 0027
die() { echo "OPS Kafka: $1" >&2; exit 1; }
namespace=${POD_NAMESPACE:-openserverless}
pod=${HOSTNAME:?missing pod name}
case "$namespace" in ''|*[!a-z0-9-]*) die invalid_namespace;; esac
case "$pod" in kafka-0) ;; *) die unsupported_single_node_identity;; esac
[ -d /kafka ] && [ -w /kafka ] || die data_mount_not_writable
# Never create a second layout beside legacy, foreign or unrecognized data.
for path in /kafka/.[!.]* /kafka/..?* /kafka/*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    [ "$path" = /kafka/kraft-logs ] && [ ! -L "$path" ] && [ -d "$path" ] || die foreign_data_requires_explicit_migration
done
data=/kafka/kraft-logs
[ ! -L "$data" ] || die symlink_data_directory
mkdir -p "$data" /tmp/ops-kafka /tmp/ops-kafka-logs
conf=/tmp/ops-kafka/server.properties
cat >"$conf" <<EOF
process.roles=broker,controller
node.id=0
controller.quorum.voters=0@${pod}.kafka.${namespace}.svc.cluster.local:9093
controller.listener.names=CONTROLLER
listeners=EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
advertised.listeners=EXTERNAL://${pod}.kafka.${namespace}.svc.cluster.local:9092
listener.security.protocol.map=EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT
inter.broker.listener.name=EXTERNAL
log.dirs=$data
num.partitions=1
default.replication.factor=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
min.insync.replicas=1
log.retention.hours=168
group.initial.rebalance.delay.ms=0
EOF
meta=$data/meta.properties
if [ -f "$meta" ] && [ ! -L "$meta" ]; then
    # Format version zero is ZooKeeper storage, even if mounted at this path.
    for key in version 'node[.]id' 'cluster[.]id' 'directory[.]id'; do
        [ "$(grep -Ec "^${key}=" "$meta")" = 1 ] || die ambiguous_or_missing_metadata_key
    done
    [ "$(grep -c '^version=1$' "$meta")" = 1 ] || die non_kraft_or_invalid_metadata
    [ "$(grep -c '^node[.]id=0$' "$meta")" = 1 ] || die mismatched_node_identity
    [ "$(grep -Ec '^cluster[.]id=[A-Za-z0-9_-]{22}$' "$meta")" = 1 ] || die invalid_cluster_identity
    [ "$(grep -Ec '^directory[.]id=[A-Za-z0-9_-]{22}$' "$meta")" = 1 ] || die invalid_directory_identity
    echo 'OPS Kafka: reuse verified KRaft identity; storage is not formatted again'
else
    [ -z "$(find "$data" -mindepth 1 -maxdepth 1 -print -quit)" ] || die nonempty_unformatted_storage
    echo 'OPS Kafka: initialize empty KRaft storage once'
    # Kafka's logging may also write stdout. Accept exactly one UUID line;
    # never persist diagnostic output as the cluster identity.
    generated=$(/opt/kafka/bin/kafka-storage.sh random-uuid) || die uuid_generation_failed
    cluster=$(printf '%s\n' "$generated" | grep -E '^[A-Za-z0-9_-]{22}$') || die invalid_generated_uuid
    [ "${#cluster}" = 22 ] || die ambiguous_generated_uuid
    /opt/kafka/bin/kafka-storage.sh format -t "$cluster" -c "$conf"
fi
export KAFKA_OPTS='-javaagent:/opt/ops-kafka/jmx.jar=0.0.0.0:9071:/opt/ops-kafka/jmx.yml'
echo 'OPS Kafka: start single-node broker/controller; no ZooKeeper connection'
exec /opt/kafka/bin/kafka-server-start.sh "$conf"
