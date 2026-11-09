#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Verify the CVE-2026-6653 vulnerable/fixed source boundary by exact hash."""
import argparse
import hashlib
import json
from pathlib import Path

BASE_SHA256 = "8465758face958ddab5ed16adcd02a0225f02c01a8707940c9e10dbac07fc815"
PATCHED_SHA256 = "2bd24cfd120751a18072f9f6fb1209b8c3f69c511f33bad4979252b30136ddf5"
PATCH_SHA256 = "4c895896b19d5cb15a1aa9d92beb9611fc56e78fece5dcac4e182cca9aa4ff21"
RUNTIME_DEB_SHA256 = "06cb2780944dc5b236aeed459c541fe7e1a106cdffe65243a2134ad8d569ee69"
INSTALLED_LIBRARY_SHA256 = "34bb9f77c4e326b15eb19febca4fdde20fe8a5982629485aa96a8d72e040b57f"

VULNERABLE_ENTITY_CONTINUATION = "if (xmlParserEntityCheck(ctxt, 0, entity, 0))\n\t        return;"
FIXED_ENTITY_CONTINUATION = ("if (xmlParserEntityCheck(ctxt, 0, entity, 0) ||\n"
                             "\t        (ctxt->instate == XML_PARSER_EOF))\n"
                             "\t        return;")
VULNERABLE_SUBSET_CONTINUATION = ("xmlParseMarkupDecl(ctxt);\n"
                                  "\t    xmlParsePEReference(ctxt);\n\n"
                                  "            /*")
FIXED_SUBSET_CONTINUATION = ("xmlParseMarkupDecl(ctxt);\n"
                             "\t    xmlParsePEReference(ctxt);\n"
                             "\t    if (ctxt->instate == XML_PARSER_EOF)\n"
                             "\t        return;\n\n"
                             "            /*")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-source", type=Path, required=True)
    parser.add_argument("--patched-source", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.base_source.read_text()
    patched = args.patched_source.read_text()
    checks = {
        "exact_debian_base_source": digest(args.base_source) == BASE_SHA256,
        "exact_patched_source": digest(args.patched_source) == PATCHED_SHA256,
        "exact_backport_patch": digest(args.patch) == PATCH_SHA256,
        "base_continues_after_entity_halt":
            VULNERABLE_ENTITY_CONTINUATION in base
            and FIXED_ENTITY_CONTINUATION not in base,
        "base_continues_after_parameter_entity_halt":
            VULNERABLE_SUBSET_CONTINUATION in base
            and FIXED_SUBSET_CONTINUATION not in base,
        "candidate_stops_after_entity_halt":
            FIXED_ENTITY_CONTINUATION in patched
            and VULNERABLE_ENTITY_CONTINUATION not in patched,
        "candidate_stops_after_parameter_entity_halt":
            FIXED_SUBSET_CONTINUATION in patched
            and VULNERABLE_SUBSET_CONTINUATION not in patched,
    }
    report = {"checks": [{"check": name, "status": "PASS" if ok else "FAIL"}
                         for name, ok in checks.items()]}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
