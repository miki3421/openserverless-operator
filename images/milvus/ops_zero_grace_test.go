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

package proxy

import (
	"testing"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"

	"github.com/milvus-io/milvus-proto/go-api/v3/commonpb"
	"github.com/milvus-io/milvus-proto/go-api/v3/milvuspb"
)

// Exercise both the explicit request path and the collection default path.
// Positive bounded grace and legacy guarantee timestamps retain their contract.
func TestOPSZeroGraceTimestampAllocation(t *testing.T) {
	original := Params.CommonCfg.GracefulTime.GetValue()
	t.Cleanup(func() { Params.Save(Params.CommonCfg.GracefulTime.Key, original) })
	for _, tc := range []struct {
		name      string
		grace     string
		level     commonpb.ConsistencyLevel
		guarantee uint64
		skip      bool
	}{
		{"bounded_zero", "0", commonpb.ConsistencyLevel_Bounded, 0, false},
		{"bounded_positive", "5000", commonpb.ConsistencyLevel_Bounded, 0, true},
		{"strong", "0", commonpb.ConsistencyLevel_Strong, 0, false},
		{"session", "0", commonpb.ConsistencyLevel_Session, 100, true},
		{"eventually", "0", commonpb.ConsistencyLevel_Eventually, 0, true},
		{"customized", "0", commonpb.ConsistencyLevel_Customized, 100, true},
		{"legacy_explicit_timestamp", "0", commonpb.ConsistencyLevel_Strong, 100, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			require.NoError(t, Params.Save(Params.CommonCfg.GracefulTime.Key, tc.grace))
			search := &searchTask{request: &milvuspb.SearchRequest{
				ConsistencyLevel: tc.level, GuaranteeTimestamp: tc.guarantee,
			}}
			query := &queryTask{request: &milvuspb.QueryRequest{
				ConsistencyLevel: tc.level, GuaranteeTimestamp: tc.guarantee,
			}}
			require.Equal(t, tc.skip, search.CanSkipAllocTimestamp(), "search")
			require.Equal(t, tc.skip, query.CanSkipAllocTimestamp(), "query")
			if tc.guarantee != 0 {
				return
			}
			cache := NewMockCache(t)
			cache.EXPECT().GetCollectionID(mock.Anything, mock.Anything, mock.Anything).Return(UniqueID(123), nil)
			cache.EXPECT().GetCollectionInfo(mock.Anything, mock.Anything, mock.Anything, mock.Anything).
				Return(&collectionInfo{CollID: 123, ConsistencyLevel: tc.level}, nil)
			search.baseTask = baseTask{MetaCache: cache}
			query.baseTask = baseTask{MetaCache: cache}
			search.request.UseDefaultConsistency = true
			query.request.UseDefaultConsistency = true
			require.Equal(t, tc.skip, search.CanSkipAllocTimestamp(), "search collection default")
			require.Equal(t, tc.skip, query.CanSkipAllocTimestamp(), "query collection default")
		})
	}
}

func TestOPSZeroGraceConsistencyBoundary(t *testing.T) {
	original := Params.CommonCfg.GracefulTime.GetValue()
	t.Cleanup(func() { Params.Save(Params.CommonCfg.GracefulTime.Key, original) })
	const timestamp = uint64(50000000000)
	for _, grace := range []string{"0", "5000"} {
		require.NoError(t, Params.Save(Params.CommonCfg.GracefulTime.Key, grace))
		for _, level := range []commonpb.ConsistencyLevel{
			commonpb.ConsistencyLevel_Strong, commonpb.ConsistencyLevel_Bounded,
			commonpb.ConsistencyLevel_Session, commonpb.ConsistencyLevel_Eventually,
			commonpb.ConsistencyLevel_Customized,
		} {
			require.Equal(t, grace == "0" && level == commonpb.ConsistencyLevel_Bounded,
				zeroGraceBoundedConsistency(level))
		}
		if grace == "0" {
			require.Equal(t, parseGuaranteeTsFromConsistency(0, timestamp, commonpb.ConsistencyLevel_Strong),
				parseGuaranteeTsFromConsistency(0, timestamp, commonpb.ConsistencyLevel_Bounded))
		}
	}
}
