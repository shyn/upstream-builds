#!/usr/bin/env bash
# Called from the upstream checkout, with ARTIFACT_DIR provided by the hub.
set -euo pipefail
test "$(uname -m)" = arm64
rustup show active-toolchain
rustup target add aarch64-apple-darwin
cargo --version

tools/release/data-fetch.sh
QINGJIAN_TARGET=aarch64-apple-darwin apps/macos/scripts/bundle.sh --pkg

app=target/Qingjian.app
codesign --verify --deep --strict "$app"
lipo "$app/Contents/MacOS/qingjian-macos" -verify_arch arm64
plutil -lint "$app/Contents/Info.plist"
test -s "$app/Contents/Resources/dict.qj"
test -s "$app/Contents/Resources/models/hanzhang-zhiwei/hanzhang-zhiwei-small.qjm"
test -s "$app/Contents/Resources/models/hanzhang-tongbian/hanzhang-tongbian-small.qjm"
packages=(target/pkg/*-macos-arm64.pkg)
test "${#packages[@]}" -eq 1
test -s "${packages[0]}"
pkgutil --expand "${packages[0]}" "$RUNNER_TEMP/pkg-check"
cp "${packages[0]}" "$ARTIFACT_DIR/"
cp tools/release/data.lock "$ARTIFACT_DIR/data.lock"
printf '%s\n' 'ad-hoc signed application; installer not Developer ID signed or notarized' > "$ARTIFACT_DIR/SIGNING.txt"
