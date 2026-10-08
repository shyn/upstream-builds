#!/usr/bin/env python3
"""Run the real state CLI against a local HTTP GitHub API stand-in.

This verifies state transitions, not GitHub runner builds or artifact uploads.
Run: python3 verification/scenarios.py
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
SHA_A, SHA_B, OWN_SHA = "a" * 40, "b" * 40, "c" * 40
state = {"head": SHA_A, "refs": {}, "tags": {}, "fault": None, "calls": []}
results = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def do_PATCH(self):
        self.handle_api("PATCH")

    def handle_api(self, method):
        path = self.path
        payload = None
        if self.headers.get("Content-Length"):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["calls"].append({"method": method, "path": path, "payload": payload})
        fault = state["fault"]
        if fault and method == fault[0] and fault[1] in path:
            return self.reply(fault[2], {"message": "injected failure"})
        if method == "GET" and "/git/ref/heads/" in path:
            return self.reply(200, {"object": {"type": "commit", "sha": state["head"]}})
        if method == "GET" and "/git/ref/tags/" in path:
            tag = path.split("/git/ref/tags/")[1]
            sha = state["refs"].get(tag)
            return self.reply(200 if sha else 404, {"object": {"type": "tag", "sha": sha}})
        if method == "GET" and "/git/tags/" in path:
            sha = path.rsplit("/", 1)[1]
            return self.reply(200, state["tags"][sha])
        if method == "POST" and path.endswith("/git/tags"):
            sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            state["tags"][sha] = payload
            return self.reply(201, {"sha": sha})
        if method == "POST" and path.endswith("/git/refs"):
            tag = payload["ref"].removeprefix("refs/tags/")
            if tag in state["refs"]:
                return self.reply(422, {"message": "already exists"})
            state["refs"][tag] = payload["sha"]
            return self.reply(201, {"object": {"sha": payload["sha"]}})
        if method == "PATCH" and "/git/refs/tags/" in path:
            tag = path.split("/git/refs/tags/")[1]
            state["refs"][tag] = payload["sha"]
            return self.reply(200, {"object": {"sha": payload["sha"]}})
        self.reply(404, {"message": "unknown API"})

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_env = {
    **os.environ, "GITHUB_API_URL": f"http://127.0.0.1:{server.server_port}",
    "GITHUB_REPOSITORY": "example/builder", "GITHUB_SHA": OWN_SHA,
    "UPSTREAM_REPOSITORY": "chenyukang/qingjian", "UPSTREAM_BRANCH": "main",
    "BUILD_PROFILE": "macos-arm64", "GH_TOKEN": "local-test-token",
    "UPSTREAM_TOKEN": "", "FORCE_BUILD": "false",
    "GITHUB_STEP_SUMMARY": "", "RUN_URL": "https://example.invalid/runs/1",
    "ARTIFACT_URL": "https://example.invalid/artifacts/1",
}


def cli(mode, expect_success=True, **extra):
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp) / "output"
        env = {**base_env, **extra, "GITHUB_OUTPUT": str(output)}
        process = subprocess.run([sys.executable, str(ROOT / ".github/scripts/upstream.py"), mode],
                                 env=env, capture_output=True, text=True, timeout=10)
        assert (process.returncode == 0) == expect_success, process.stdout + process.stderr
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return values


def record(check, sha, expect_success=True):
    cli("record", expect_success, EXPECTED_STATE=check["state_object"], UPSTREAM_SHA=sha)


def passed(name):
    results.append({"scenario": name, "result": "PASS"})


try:
    first = cli("check")
    assert first["should_build"] == "true" and not state["refs"]
    assert not any(c["method"] != "GET" for c in state["calls"])
    passed("first check requests build without modifying state")
    record(first, SHA_A)
    unchanged = cli("check")
    assert unchanged["should_build"] == "false"
    passed("successful publication records SHA; unchanged SHA skips")
    forced = cli("check", FORCE_BUILD="true")
    assert forced["should_build"] == "true"
    passed("manual force rebuilds unchanged SHA")
    state["head"] = SHA_B
    changed = cli("check")
    assert changed["sha"] == SHA_B and changed["should_build"] == "true"
    passed("changed upstream SHA requests build")
    for name in ("build failure", "missing output", "artifact upload failure", "release failure"):
        before = dict(state["refs"])
        # These workflow failures must prevent record from running.
        retry = cli("check")
        assert retry["should_build"] == "true" and state["refs"] == before
        passed(f"{name}: when record is not invoked, next check retries")
    for method, path, statuses in (
        ("GET", "/git/ref/heads/", (404, 403, 429, 500)),
        ("GET", "/git/ref/tags/", (403, 500)),
    ):
        for status in statuses:
            state["fault"] = (method, path, status)
            cli("check", False)
            passed(f"API {path} HTTP {status} fails instead of silently skipping")
    state["fault"] = ("PATCH", "/git/refs/tags/", 403)
    before = dict(state["refs"])
    record(changed, SHA_B, False)
    assert state["refs"] == before
    state["fault"] = None
    assert cli("check")["should_build"] == "true"
    passed("state write failure keeps old success and remains retryable")
    record(changed, SHA_B)
    assert cli("check")["should_build"] == "false"
    passed("retry success advances marker to new SHA")
    before = dict(state["refs"])
    record(forced, SHA_A, False)
    assert state["refs"] == before
    passed("stale rerun cannot overwrite newer success")
    tag_sha = next(iter(state["refs"].values()))
    original = state["tags"][tag_sha]["message"]
    for corrupt in ("not-json", json.dumps({"repository": "other/repo"})):
        state["tags"][tag_sha]["message"] = corrupt
        cli("check", False)
    state["tags"][tag_sha]["message"] = original
    passed("corrupt or mismatched marker fails closed")
    other = cli("check", UPSTREAM_BRANCH="other")
    assert other["should_build"] == "true" and other["state_object"] == "missing"
    other_repo = cli("check", UPSTREAM_REPOSITORY="other/repo")
    assert other_repo["state_tag"] != changed["state_tag"]
    passed("repository and branch changes use separate success markers")
    assert cli("check")["sha"] == SHA_B
    receipt = {
        "scope": "local HTTP API scenario verification; no GitHub runner or real uploads",
        "passed": len(results), "failed": 0, "scenarios": results,
        "final_refs": state["refs"], "api_calls": state["calls"],
    }
    (ROOT / "verification/scenario-report.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"PASS: {len(results)} local flow scenarios; verification/scenario-report.json")
finally:
    server.shutdown()
    server.server_close()
