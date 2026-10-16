#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Build MindBroadcast.app from the local virtualenv.
#
#   packaging/macos/build.sh [--run]
#
# What it produces
#   build/MindBroadcast.app — a native macOS application bundle whose
#   executable launches the venv's Python, starts the TTS + console servers
#   if they are not already running, and opens a native window on the console.
#
# What it deliberately does NOT do
#   - It does not bundle a Python interpreter or the model weights. The app
#     points at the project's .venv, so a rebuild always reflects current code,
#     and the 18GB model stays where the user put it (Ollama + HF cache).
#   - It does not sign/notarize. An unsigned app needs a right-click -> Open the
#     first time; see README.
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/build"
APP="$OUT/MindBroadcast.app"

cd "$ROOT"

# ── preconditions ────────────────────────────────────────────────────
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "ERROR: .venv not found. Run:"
    echo "    /opt/homebrew/bin/python3.13 -m venv .venv"
    echo "    .venv/bin/pip install -r requirements.txt"
    echo "    .venv/bin/pip install fastapi uvicorn pywebview"
    exit 1
fi

echo "▶ building MindBroadcast.app"

# ── bundle layout ────────────────────────────────────────────────────
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/MindBroadcast" <<'LAUNCHER'
#!/usr/bin/env bash
# The app's executable: resolve the project root from this bundle's location,
# then run the desktop shell with the project's virtualenv.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_ROOT="$(cd "$APP_DIR/../../.." && pwd)"

# A .app sitting inside the project (build/…) resolves to the project. If it
# has been copied elsewhere, fall back to the recorded path.
if [[ ! -d "$PROJECT_ROOT/.venv" && -f "$APP_DIR/Contents/Resources/project_root" ]]; then
    PROJECT_ROOT="$(cat "$APP_DIR/Contents/Resources/project_root")"
fi

cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m core.desktop
LAUNCHER
chmod +x "$APP/Contents/MacOS/MindBroadcast"

# Remember where the project lives, so a moved .app still finds it.
printf '%s\n' "$ROOT" > "$APP/Contents/Resources/project_root"

cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

# Icon (optional; skip gracefully if iconutil isn't available).
if [[ -f "$HERE/icon.iconset/icon_512x512@2x.png" ]]; then
    if command -v iconutil > /dev/null 2>&1; then
        iconutil -c icns "$HERE/icon.iconset" -o "$APP/Contents/Resources/icon.icns" \
            && echo "  ✓ icon.icns"
    fi
else
    echo "  (no icon.iconset — using the default icon)"
fi

echo "✓ built $APP"

if [[ "${1:-}" == "--run" ]]; then
    echo "▶ launching…"
    open "$APP"
    exit 0
fi

cat <<EOF

To run it:
    open "$APP"

Note: the app is unsigned. The first launch needs right-click -> Open, or:
    xattr -dr com.apple.quarantine "$APP"

EOF
