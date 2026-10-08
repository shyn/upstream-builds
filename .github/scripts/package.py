#!/usr/bin/env python3
"""Require real build output, then add source, environment receipt, and checksums."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

source = Path(os.environ["SOURCE_DIR"])
dest = Path(os.environ["ARTIFACT_DIR"])
payload = [p for p in dest.rglob("*") if p.is_file() and p.stat().st_size]
if not payload:
    raise SystemExit("Build script produced no nonempty files; refusing to mark success")
if any(p.is_symlink() for p in dest.rglob("*")):
    raise SystemExit("Artifact output must contain regular files, not symbolic links")
actual_sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
if actual_sha != os.environ["UPSTREAM_SHA"]:
    raise SystemExit("Checkout SHA does not match the checked upstream SHA")
with (dest / "source.tar.gz").open("wb") as archive:
    with gzip.GzipFile(fileobj=archive, mode="wb", filename="", mtime=0) as compressed:
        process = subprocess.Popen(["git", "-C", str(source), "archive", "--format=tar",
                                    "--prefix=upstream-source/", "HEAD"], stdout=subprocess.PIPE)
        while chunk := process.stdout.read(1024 * 1024):
            compressed.write(chunk)
        if process.wait():
            raise SystemExit("Source archive failed")
metadata = {
    "target": os.environ["BUILD_PROFILE"],
    "repository": os.environ["UPSTREAM_REPOSITORY"],
    "branch": os.environ["UPSTREAM_BRANCH"],
    "sha": actual_sha,
    "builder_sha": os.environ["GITHUB_SHA"],
    "run_url": os.environ["RUN_URL"],
    "runner_os": platform.platform(),
    "data_tag": os.environ.get("DATA_TAG"),
    "data_sha256": os.environ.get("DATA_SHA256"),
    "model_sha256": os.environ.get("MODEL_SHA256"),
    "p2c_model_sha256": os.environ.get("P2C_MODEL_SHA256"),
}
toolchain = source / "rust-toolchain.toml"
if toolchain.exists():
    metadata["rust_toolchain"] = toolchain.read_text()
(dest / "build-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
lines = []
for file in sorted(dest.rglob("*")):
    if not file.is_file() or file.name == "SHA256SUMS":
        continue
    with file.open("rb") as stream:
        hasher = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
        digest = hasher.hexdigest()
    name = file.relative_to(dest).as_posix()
    if any(char in name for char in "\n\r\\"):
        raise SystemExit("Artifact names must not contain newlines or backslashes")
    lines.append(f"{digest}  {name}\n")
(dest / "SHA256SUMS").write_text("".join(lines))
print(f"Collected {len(lines)} files for {metadata['target']}@{actual_sha}")
