#!/usr/bin/env bash
# tools/build-creator-macos.sh
# Builds a universal (arm64 + x86_64) macOS .app + .dmg for LiveUSBAgent Creator.
# Requires only macOS Xcode Command Line Tools (swiftc, lipo, codesign, hdiutil).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/dist}"

VERSION="$(tr -d ' \t\r\n' < "${REPO_ROOT}/VERSION")"
APP_NAME="LiveUSBAgent Creator"
BIN_NAME="LiveUSBAgentCreator"
SRC_SWIFT="${REPO_ROOT}/tools/macos-creator/LiveUSBAgentCreator.swift"

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

APP_DIR="${STAGE}/${APP_NAME}.app"
mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources"

echo "[macos-creator] Compiling arm64 + x86_64 universal binary (v${VERSION})..."
swiftc -O -target arm64-apple-macos12.0 "${SRC_SWIFT}" -o "${STAGE}/${BIN_NAME}-arm64"
swiftc -O -target x86_64-apple-macos12.0 "${SRC_SWIFT}" -o "${STAGE}/${BIN_NAME}-x86_64"
lipo -create "${STAGE}/${BIN_NAME}-arm64" "${STAGE}/${BIN_NAME}-x86_64" \
    -output "${APP_DIR}/Contents/MacOS/${BIN_NAME}"
chmod 0755 "${APP_DIR}/Contents/MacOS/${BIN_NAME}"

cat <<EOF > "${APP_DIR}/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>${BIN_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.levra7.liveusbagent.creator</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

codesign --force --deep --sign - "${APP_DIR}"

mkdir -p "${OUT_DIR}"
DMG_NAME="LiveUSBAgent-Creator-${VERSION}-macOS.dmg"
DMG_PATH="${OUT_DIR}/${DMG_NAME}"
rm -f "${DMG_PATH}"

DMG_ROOT="${STAGE}/dmg-root"
mkdir -p "${DMG_ROOT}"
cp -R "${APP_DIR}" "${DMG_ROOT}/"
ln -s /Applications "${DMG_ROOT}/Applications"

hdiutil create -volname "${APP_NAME} ${VERSION}" \
    -srcfolder "${DMG_ROOT}" \
    -ov -format UDZO \
    "${DMG_PATH}"

(cd "${OUT_DIR}" && shasum -a 256 "${DMG_NAME}" > "${DMG_NAME}.sha256")
lipo -info "${APP_DIR}/Contents/MacOS/${BIN_NAME}"
ls -lh "${DMG_PATH}" "${DMG_PATH}.sha256"
