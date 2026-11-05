"""Consolidation input: what the project extractions say about the shared levels.

Usage: ontologizer consolidation-report [FILE.json …]  (default: `paths.application` of the config)

Prints, per shared target, which projects map to it and how; candidate labels
clustered by normalised words; proposed shared concepts and variation values.
Deterministic: a coordinator (human or LLM) reads this to decide domain/task
concepts — the decision itself is not automated.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

STOP = {"a", "an", "the", "of", "and", "or", "to", "for", "agent", "session"}


def words(label: str) -> frozenset[str]:
    ws = {w.rstrip("s") for w in re.findall(r"[a-z]+", label.lower())}
    return frozenset(ws - STOP) or frozenset(ws)


def report(files: list[str]) -> None:
    targets = defaultdict(lambda: defaultdict(list))
    candidates = []
    proposed = []
    values = []
    unmapped = defaultdict(list)
    for f in sorted(files):
        doc = json.load(open(f))
        p = doc["project"]
        for c in doc["concepts"]:
            if not c.get("maps_to"):
                unmapped[p].append(c["id"])
            for m in c.get("maps_to", []):
                if m.get("target"):
                    targets[m["target"]][p].append(f"{c['id']}({m['kind']},{m.get('confidence', '?')})")
                else:
                    candidates.append((m["candidate"], m.get("candidate_level"), p, c["id"], m["kind"]))
        proposed += [(p, x) for x in doc.get("proposed_shared", [])]
        values += [(p, x) for x in doc.get("proposed_values", [])]

    print("# Shared targets\n")
    for t in sorted(targets):
        projs = targets[t]
        print(f"## {t} — {len(projs)} projects")
        for p in sorted(projs):
            print(f"- {p}: {', '.join(projs[p])}")
        print()

    print("# Candidate clusters (by shared words)\n")
    clusters: list[tuple[frozenset, list]] = []
    for cand in sorted(candidates):
        w = words(cand[0])
        for key, members in clusters:
            if w & key:
                members.append(cand)
                break
        else:
            clusters.append((w, [cand]))
    for key, members in sorted(clusters, key=lambda kv: -len({m[2] for m in kv[1]})):
        projs = sorted({m[2] for m in members})
        print(f"## {' '.join(sorted(key))} — {len(projs)} projects: {', '.join(projs)}")
        for label, level, p, cid, kind in members:
            print(f"- [{level}] {label!r} ← {p}:{cid} ({kind})")
        print()

    print("# Proposed shared concepts\n")
    for p, x in proposed:
        print(f"- {p}: [{x.get('level')}] {x.get('label')} — {x.get('definition')} (why: {x.get('why')})")
    print("\n# Proposed variation values\n")
    for p, x in values:
        print(f"- {p}: {x['dimension']}/{x['id']} {x.get('label')} — {x.get('definition')}")
    print("\n# Concepts without shared counterpart\n")
    for p in sorted(unmapped):
        print(f"- {p}: {', '.join(unmapped[p])}")

