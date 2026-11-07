# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Offline fixture: real provider rendering with Kubernetes and config writes mocked."""
import json
import sys
from unittest.mock import patch
from openserverless import kafka

data = {'name': 'kafka', 'data_volume_size': 1, 'storage_class': 'local-path',
        'affinity': False, 'tolerations': False}
with patch.object(kafka.kube, 'kubectl', side_effect=['', '{"items":[]}']), \
     patch.object(kafka.cfg_util, 'get_kafka_config_data', return_value=data), \
     patch.object(kafka.security, 'apply', side_effect=lambda spec, _: spec), \
     patch.object(kafka.util, 'wait_for_pod_ready'), \
     patch.object(kafka, 'get_kafka_connect_data', return_value='kafka:9092'), \
     patch.object(kafka.cfg, 'put'):
    json.dump(kafka.create(), sys.stdout)
