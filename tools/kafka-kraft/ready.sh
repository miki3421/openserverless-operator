#!/bin/sh
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
set -eu
# The CLI must contact the broker; --version would succeed while it is down.
exec timeout 8 /opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092
