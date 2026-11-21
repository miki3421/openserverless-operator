# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# Rebuild the four operator CLIs from pinned source/modules with patched Go.

ARG ADV_OPERATOR_BASE
FROM ${ADV_OPERATOR_BASE}

ARG OPERATOR_REVISION
LABEL org.opencontainers.image.revision="${OPERATOR_REVISION}"
LABEL org.opencontainers.image.title="ADV Operator tenant service enablement"

COPY --chown=openserverless:openserverless \
  openserverless/couchdb_util.py \
  openserverless/milvus_admin_client.py \
  openserverless/milvus_simple_client.py \
  openserverless/milvus_standalone.py \
  openserverless/tenant_services.py \
  openserverless/user_handlers.py \
  /home/openserverless/openserverless/
