// Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
// Local regression checks for the x/net/html advisories fixed in v0.62.0.
// Run this file from the pinned Prometheus module, before and after its patch.
package htmlsecurity_test

import (
	"bytes"
	"runtime"
	"strings"
	"testing"

	"golang.org/x/net/html"
)

func TestOrdinaryHTML(t *testing.T) {
	doc, err := html.Parse(strings.NewReader("<p>synthetic &amp; safe</p>"))
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if err := html.Render(&out, doc); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "<p>synthetic &amp; safe</p>") {
		t.Fatal("ordinary text changed")
	}
}

func TestForeignNamespaceLiteralText(t *testing.T) {
	for _, element := range []string{"mglyph", "malignmark"} {
		t.Run(element, func(t *testing.T) {
			doc, err := html.Parse(strings.NewReader("<math><mtext><table><" + element + "><svg><desc><style><img src=synthetic>"))
			if err != nil {
				t.Fatal(err)
			}
			var out bytes.Buffer
			if err := html.Render(&out, doc); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(out.String(), "&lt;img src=synthetic&gt;") {
				t.Fatal("literal text became markup")
			}
		})
	}
}

func TestCoalescingAllocationBound(t *testing.T) {
	for _, prefix := range []string{"", "<table>"} {
		t.Run("prefix="+prefix, func(t *testing.T) {
			input := strings.Repeat("a</x>", 10000)
			runtime.GC()
			var before, after runtime.MemStats
			runtime.ReadMemStats(&before)
			_, err := html.Parse(strings.NewReader(prefix + input))
			runtime.ReadMemStats(&after)
			if err != nil {
				t.Fatal(err)
			}
			if used := after.TotalAlloc - before.TotalAlloc; used > 5*1024*1024 {
				t.Fatalf("small synthetic document allocated %d bytes; maximum 5 MiB", used)
			}
		})
	}
}
