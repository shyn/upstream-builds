#!/usr/bin/env python3
"""Repeatable local integration checks for manifest planning and artifact packaging."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
results = []


def run(args, cwd, env=None, ok=True):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


def passed(name):
    results.append({"scenario": name, "result": "PASS"})


with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    hub = temp / "hub"
    shutil.copytree(ROOT / ".github/scripts", hub / ".github/scripts")
    (hub / "builds").mkdir()
    (hub / "builds/fixture.sh").write_text('mkdir -p "$ARTIFACT_DIR/subdir"\nprintf "payload\\n" > "$ARTIFACT_DIR/subdir/result.txt"\n')
    target = {"id": "sample-linux", "repository": "example/source", "branch": "main",
              "runner": "ubuntu-24.04", "build_script": "builds/fixture.sh"}
    target2 = {**target, "id": "sample-macos", "runner": "macos-26"}
    config = hub / "upstreams.json"
    output = temp / "plan-output"
    env = {**os.environ, "GITHUB_OUTPUT": str(output), "SELECT_TARGET": "all"}

    def plan(targets, ok=True, selection="all"):
        config.write_text(json.dumps({"targets": targets}))
        output.write_text("")
        run([sys.executable, ".github/scripts/plan.py"], hub,
            {**env, "SELECT_TARGET": selection}, ok=ok)
        if ok:
            return json.loads(output.read_text().split("=", 1)[1])

    matrix = plan([target, target2])
    assert len(matrix["include"]) == 2
    passed("one manifest dispatches multiple independent upstream targets")
    assert plan([target, target2], selection="sample-macos")["include"] == [target2]
    passed("manual target selection dispatches exactly the selected target")
    plan([target], ok=False, selection="missing")
    passed("unknown manual target fails")
    for name, targets in (
        ("empty manifest", []),
        ("duplicate IDs", [target, target]),
        ("invalid repository", [{**target, "repository": "invalid"}]),
        ("missing build script", [{**target, "build_script": "builds/missing.sh"}]),
        ("path traversal", [{**target, "build_script": "builds/../../outside.sh"}]),
    ):
        plan(targets, ok=False)
        passed(f"{name} fails during plan")

    source = temp / "source"
    source.mkdir()
    run(["git", "init", "-b", "main"], source)
    (source / "README.md").write_text("Local integration fixture\n")
    (source / "LICENSE").write_text("Fixture content only\n")
    run(["git", "add", "README.md", "LICENSE"], source)
    run(["git", "-c", "user.name=Local Verification", "-c", "user.email=local@example.invalid",
         "commit", "-m", "Create verification fixture"], source)
    sha = run(["git", "rev-parse", "HEAD"], source).stdout.strip()
    dest = temp / "output"
    dest.mkdir()
    package_env = {**os.environ, "SOURCE_DIR": str(source), "ARTIFACT_DIR": str(dest),
                   "UPSTREAM_SHA": sha, "BUILD_PROFILE": "sample-linux",
                   "UPSTREAM_REPOSITORY": "example/source", "UPSTREAM_BRANCH": "main",
                   "GITHUB_SHA": "c" * 40, "RUN_URL": "https://example.invalid/run/1"}
    command = [sys.executable, str(hub / ".github/scripts/package.py")]
    run(command, source, package_env, ok=False)
    passed("empty build output is rejected before metadata is added")
    run(["bash", str(hub / "builds/fixture.sh")], source, package_env)
    run(command, source, {**package_env, "UPSTREAM_SHA": "f" * 40}, ok=False)
    passed("mismatched checkout SHA is rejected")
    run(command, source, package_env)
    for line in (dest / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest
    assert "subdir/result.txt" in (dest / "SHA256SUMS").read_text()
    with tarfile.open(dest / "source.tar.gz", "r:gz") as archive:
        assert "upstream-source/LICENSE" in archive.getnames()
    assert json.loads((dest / "build-info.json").read_text())["sha"] == sha
    passed("real Git source archive, nested payload, receipt and checksums verify")

report = {"scope": "local manifest and packaging integration; no real runner or uploads",
          "passed": len(results), "failed": 0, "scenarios": results}
(ROOT / "verification/hub-report.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"PASS: {len(results)} hub scenarios; verification/hub-report.json")
