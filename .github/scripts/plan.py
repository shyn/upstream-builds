#!/usr/bin/env python3
"""Validate the upstream manifest and emit a reusable-workflow matrix."""
import json
import os
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
try:
    targets = json.loads((root / "upstreams.json").read_text())["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 256:
        raise ValueError("targets must contain 1 to 256 entries")
    ids = set()
    for target in targets:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", target["id"]):
            raise ValueError("Invalid target id")
        if target["id"] in ids:
            raise ValueError(f"Duplicate target id: {target['id']}")
        ids.add(target["id"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target["repository"]):
            raise ValueError("Invalid upstream repository")
        if not target["branch"] or any(ord(c) < 32 for c in target["branch"]):
            raise ValueError("Invalid branch")
        if target["runner"] not in {
            "ubuntu-24.04", "ubuntu-26.04", "macos-15", "macos-26", "windows-2025",
        }:
            raise ValueError("Unsupported runner; explicitly add new supported labels to plan.py")
        script = target["build_script"]
        if not re.fullmatch(r"builds/[A-Za-z0-9_./-]+\.sh", script):
            raise ValueError("build_script must be a shell script in builds/")
        path = (root / script).resolve()
        if not path.is_relative_to(root / "builds") or not path.is_file():
            raise ValueError(f"Missing or out-of-bounds build script: {script}")
    selected = os.environ.get("SELECT_TARGET", "all").strip() or "all"
    if selected != "all" and selected not in ids:
        raise ValueError(f"Unknown target {selected!r}; available: {', '.join(sorted(ids))}")
    matrix = {"include": [t for t in targets if selected == "all" or t["id"] == selected]}
    value = json.dumps(matrix, separators=(",", ":"))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"matrix={value}\n")
    print(json.dumps(matrix, indent=2))
except (KeyError, TypeError, ValueError) as error:
    print(f"Error: {error}", file=sys.stderr)
    sys.exit(1)
