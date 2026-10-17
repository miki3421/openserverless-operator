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

FROM golang:1.26.6-bookworm@sha256:116d58cbd88c1297624acc6e967a060012422bacf9930927e23fb719189c6f36 AS probe
WORKDIR /src
COPY health.go health_test.go ./
RUN gofmt -w health.go health_test.go && go test health.go health_test.go && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ops-etcd-health health.go
FROM gcr.io/etcd-development/etcd:v3.7.2@sha256:7c6c239825d00e3f6328a69caafd54be92063acf0c2ce78b8394699f52b75dc3
COPY --from=probe /out/ops-etcd-health /usr/local/bin/ops-etcd-health
LABEL org.opencontainers.image.description="Upstream etcd 3.7.2 with an OPS authentication-aware health probe"
