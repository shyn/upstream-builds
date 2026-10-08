#!/usr/bin/env python3
"""Verify a downloaded Qingjian artifact on macOS without installing or launching it.

Usage: python3 verification/inspect-macos-package.py ARTIFACT_DIRECTORY EXPECTED_SHA
"""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
expected_sha = sys.argv[2]


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


verified = []
for line in (root / "SHA256SUMS").read_text().splitlines():
    expected, name = line.split("  ", 1)
    file = (root / name).resolve()
    assert file.is_relative_to(root) and file.is_file(), f"Invalid manifest path: {name}"
    assert digest(file) == expected, f"Checksum mismatch: {name}"
    verified.append(name)
metadata = json.loads((root / "build-info.json").read_text())
assert metadata["sha"] == expected_sha, "Unexpected upstream SHA"
packages = list(root.glob("*-macos-arm64.pkg"))
assert len(packages) == 1, "Expected exactly one macOS arm64 installer"
package = packages[0]
with tempfile.TemporaryDirectory() as temp:
    expanded = Path(temp) / "expanded"
    subprocess.run(["pkgutil", "--expand-full", str(package), str(expanded)], check=True)
    apps = list(expanded.rglob("Qingjian.app"))
    assert len(apps) == 1, "Installer must contain one Qingjian.app"
    app = apps[0]
    binary = app / "Contents/MacOS/qingjian-macos"
    subprocess.run(["lipo", str(binary), "-verify_arch", "arm64"], check=True)
    archs = subprocess.check_output(["lipo", str(binary), "-archs"], text=True).strip()
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    data_files = ["dict.qj", "models/hanzhang-zhiwei/hanzhang-zhiwei-small.qjm",
                  "models/hanzhang-tongbian/hanzhang-tongbian-small.qjm"]
    sizes = {}
    for name in data_files:
        file = app / "Contents/Resources" / name
        assert file.is_file() and file.stat().st_size, f"Missing product data: {name}"
        sizes[name] = file.stat().st_size
report = {
    "result": "PASS", "scope": "downloaded package verification; not installed or launched",
    "upstream_sha": expected_sha, "builder_sha": metadata["builder_sha"],
    "package": package.name, "package_sha256": digest(package),
    "package_size": package.stat().st_size, "architectures": archs,
    "version": info["CFBundleShortVersionString"], "build_number": info["CFBundleVersion"],
    "codesign_verification": "PASS", "product_data_bytes": sizes,
    "checksum_verified_files": verified,
}
destination = Path(__file__).resolve().parent / "package-report.json"
destination.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
