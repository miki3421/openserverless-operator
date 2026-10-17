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

import openserverless.redis as redis


class RedisClient:
    """Administrator-only quota measurements and durable tenant ACL transitions."""
    def __init__(self, redis_password):
        self._client=redis.admin_client(password=redis_password)

    def calculate_prefix_allocated_size(self, prefix):
        prefix=redis._prefix(prefix)
        return sum(self._client.memory_usage(key) or 0 for key in self._client.scan_iter(match=prefix+'*',count=200))

    def set_prefix_readonly(self, namespace, prefix):
        return redis.set_user_readonly(namespace,prefix,True)

    def set_prefix_all(self, namespace, prefix):
        return redis.set_user_readonly(namespace,prefix,False)
