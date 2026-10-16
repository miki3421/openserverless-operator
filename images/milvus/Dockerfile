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

FROM golang:1.26.6-bookworm@sha256:116d58cbd88c1297624acc6e967a060012422bacf9930927e23fb719189c6f36 AS go-toolchain
FROM docker.io/milvusdb/milvus:v3.0.2@sha256:5f13bf88e110a517911c3e6dd8172454e90042c21e606a868084615a4302c8a0 AS build
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config git ca-certificates curl patch libjemalloc-dev && rm -rf /var/lib/apt/lists/*
COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:$PATH
ENV PKG_CONFIG_PATH=/pkgconfig
ENV LD_LIBRARY_PATH=/milvus/lib
WORKDIR /src
# Header revisions match the native libraries retained from the pinned image.
RUN curl -fsSL https://codeload.github.com/milvus-io/milvus/tar.gz/3c4448a2aee506ccd14851e742aba85f1c83188a | tar xz --strip-components=1 && \
    mkdir /storage-headers /common-headers /kafka-headers /rocks-headers /pkgconfig /out && \
    curl -fsSL https://codeload.github.com/milvus-io/milvus-storage/tar.gz/f38765d83a35d729a7cbfe8e1caed6246d45e06d | tar xz --strip-components=1 -C /storage-headers && \
    curl -fsSL https://codeload.github.com/zilliztech/milvus-common/tar.gz/7d67e14eae701d99227eed582e3af851fa51bee4 | tar xz --strip-components=1 -C /common-headers && \
    curl -fsSL https://codeload.github.com/confluentinc/librdkafka/tar.gz/cb8c19c43011b66c4b08b25e5150455a247e1ff3 | tar xz --strip-components=1 -C /kafka-headers && \
    mkdir /kafka-headers/librdkafka && cp /kafka-headers/src/*.h /kafka-headers/librdkafka/ && \
    curl -fsSL https://codeload.github.com/facebook/rocksdb/tar.gz/da11a59034584ea2d0911268b8136e5249d6b692 | tar xz --strip-components=1 -C /rocks-headers
RUN printf 'Name: Milvus Core\nDescription: Pinned runtime libraries\nVersion: 3.0.2\nLibs: -L/milvus/lib -lmilvus_core\nCflags: -I/src/internal/core/src -I/common-headers/include\n' >/pkgconfig/milvus_core.pc && \
    printf 'Name: Milvus Storage\nDescription: Pinned runtime libraries\nVersion: 3.0.2\nLibs: -L/milvus/lib -lmilvus-storage\nCflags: -I/storage-headers/cpp/include\n' >/pkgconfig/milvus-storage.pc && \
    printf 'Name: Rdkafka\nDescription: Pinned runtime libraries\nVersion: 2.6.1\nLibs: -L/milvus/lib -lrdkafka\nCflags: -I/kafka-headers\n' >/pkgconfig/rdkafka.pc && \
    printf 'Name: RocksDB\nDescription: Pinned runtime libraries\nVersion: 6.29.5\nLibs: -L/milvus/lib -lrocksdb\nCflags: -I/rocks-headers/include\n' >/pkgconfig/rocksdb.pc
COPY zero-grace.patch /tmp/zero-grace.patch
RUN patch -p1 < /tmp/zero-grace.patch
RUN go build -p 3 -pgo=configs/pgo/default.pgo -tags dynamic,sonic,with_jemalloc,bytedance_tango -ldflags="-checklinkname=0 -r /milvus/lib -X github.com/milvus-io/milvus/cmd/milvus.MilvusVersion=3.0.2 -X github.com/milvus-io/milvus/cmd/milvus.BuildTags=3.0.2-ops-zero-grace -X github.com/milvus-io/milvus/cmd/milvus.GitCommit=3c4448a2aee506ccd14851e742aba85f1c83188a-ops" -o /out/milvus ./cmd/main.go

# Optional focused regression tests, including upstream timestamp-allocation tests.
FROM build AS test
COPY ops_zero_grace_test.go internal/proxy/ops_zero_grace_test.go
RUN go test -p 2 -tags test,dynamic,sonic,with_jemalloc,bytedance_tango -ldflags="-checklinkname=0 -r /milvus/lib" ./internal/proxy -run 'TestOPSZeroGrace|TestSearchTask_CanSkipAllocTimestamp|TestQueryTask_CanSkipAllocTimestamp' -count=1 -timeout=5m

# Only the Go server changes; all native libraries and runtime defaults are upstream.
FROM docker.io/milvusdb/milvus:v3.0.2@sha256:5f13bf88e110a517911c3e6dd8172454e90042c21e606a868084615a4302c8a0
COPY --from=build /out/milvus /milvus/bin/milvus
LABEL org.opencontainers.image.version="3.0.2-ops-zero-grace" \
      org.opencontainers.image.description="Milvus 3.0.2 with native Strong execution for zero-grace Bounded reads"
