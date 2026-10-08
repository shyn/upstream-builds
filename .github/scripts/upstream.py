#!/usr/bin/env python3
"""Check or record a successful upstream build using an annotated Git tag."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def api(method, path, token, payload=None, missing_ok=False):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "qingjian-builder"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path,
        data=data, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise RuntimeError(f"GitHub API {method} {path}: HTTP {error.code}") from None


def valid_sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full 40-character commit SHA")
    return value


def output(values):
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
            file.write(text)
    print(text, end="")


def main():
    upstream = os.environ.get("UPSTREAM_REPOSITORY", "chenyukang/qingjian").lower()
    own = os.environ["GITHUB_REPOSITORY"]
    for repo in (upstream, own):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Repository must have owner/name format")
    branch = os.environ.get("UPSTREAM_BRANCH", "main")
    profile = os.environ.get("BUILD_PROFILE", "macos-arm64")
    if not branch or any(ord(c) < 32 for c in branch + profile):
        raise ValueError("Invalid branch or profile")
    identity = {"repository": upstream, "branch": branch, "profile": profile}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    state_tag = f"build-state-{key}"
    root = f"/repos/{own}/git"
    token = os.environ.get("GH_TOKEN", "")
    state_ref = api("GET", f"{root}/ref/tags/{state_tag}", token, missing_ok=True)
    state_object = "missing"
    previous = None
    if state_ref is not None:
        if state_ref["object"]["type"] != "tag":
            raise ValueError("Build state must be an annotated tag")
        state_object = valid_sha(state_ref["object"]["sha"])
        tag = api("GET", f"{root}/tags/{state_object}", token)
        previous = json.loads(tag["message"])
        if any(previous.get(k) != v for k, v in identity.items()):
            raise ValueError("Build state repository/branch/profile mismatch")
        valid_sha(previous["sha"])

    mode = sys.argv[1]
    if mode == "check":
        upstream_token = os.environ.get("UPSTREAM_TOKEN") or token
        ref = urllib.parse.quote(branch, safe="")
        head = api("GET", f"/repos/{upstream}/git/ref/heads/{ref}", upstream_token)
        sha = valid_sha(head["object"]["sha"])
        force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
        changed = previous is None or previous["sha"] != sha
        should_build = force or changed
        reason = "forced" if force else ("changed" if changed else "unchanged")
        output({"sha": sha, "should_build": str(should_build).lower(),
                "state_object": state_object, "state_tag": state_tag})
        summary = f"Upstream: {upstream} ({branch})\nSHA: {sha}\nDecision: {reason}\n"
    elif mode == "record":
        # Workflow concurrency serializes writers; reject stale re-runs as well.
        if os.environ["EXPECTED_STATE"] != state_object:
            raise ValueError("State changed since check; re-run ALL jobs to check again")
        sha = valid_sha(os.environ["UPSTREAM_SHA"])
        record = {**identity, "sha": sha,
                  "run_url": os.environ["RUN_URL"],
                  "artifact_url": os.environ["ARTIFACT_URL"],
                  "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        tag = api("POST", f"{root}/tags", token, {
            "tag": state_tag, "message": json.dumps(record),
            "object": valid_sha(os.environ["GITHUB_SHA"]), "type": "commit",
        })
        tag_sha = valid_sha(tag["sha"])
        if state_ref is None:
            api("POST", f"{root}/refs", token,
                {"ref": f"refs/tags/{state_tag}", "sha": tag_sha})
        else:
            api("PATCH", f"{root}/refs/tags/{state_tag}", token,
                {"sha": tag_sha, "force": True})
        # A separate read confirms the successful state write.
        confirmed = api("GET", f"{root}/ref/tags/{state_tag}", token)
        if confirmed["object"]["sha"] != tag_sha:
            raise RuntimeError("Build state write could not be confirmed")
        summary = f"Recorded successful build: {upstream}@{sha}\nArtifact: {record['artifact_url']}\n"
    else:
        raise ValueError("Usage: upstream.py check|record")
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as file:
            file.write("```text\n" + summary + "```\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
