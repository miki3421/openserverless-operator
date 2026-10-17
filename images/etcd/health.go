// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"time"
)

func check(ctx context.Context, command func(context.Context, ...string) ([]byte, error)) error {
	body, err := command(ctx, "auth", "status", "--write-out=json")
	if err != nil {
		return errors.New("authentication status unavailable")
	}
	var status struct {
		Enabled bool `json:"enabled"`
	}
	if json.Unmarshal(body, &status) != nil || !status.Enabled {
		return errors.New("authentication is not enabled")
	}
	if _, err = command(ctx, "endpoint", "health"); err != nil {
		return errors.New("quorum unavailable")
	}
	return nil
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	err := check(ctx, func(ctx context.Context, args ...string) ([]byte, error) {
		return exec.CommandContext(ctx, "/usr/local/bin/etcdctl", args...).Output()
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "etcd authentication/quorum readiness failed")
		os.Exit(1)
	}
}
