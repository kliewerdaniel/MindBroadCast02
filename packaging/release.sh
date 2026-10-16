#!/usr/bin/env bash
# ── Multi-OS release pipeline for MindBroadcast ────────────────────────
# Produces (artifacts built on the host they run on):
#   releases/
#   ├── MindBroadcast-<version>-macos-x86_64.dmg      (built on macOS)
#   ├── MindBroadcast-<version>-linux-x86_64.deb      (built on Linux, via fpm)
#   ├── MindBroadcast-<version>-windows-x86_64.exe    (built on Windows, PyInstaller)
#   └── release-manifest.json
#
# Assumptions are marked below; they describe the machine that BUILDS the
# artifact, not what the artifact itself does. The .app, the .deb and the .exe
# are each produced on the host they will run on, because PyInstaller cannot
# cross-compile.
#
#   1. brief-setup.py  ... the one-file, one-decision setup (LLM + TTS)
#   2. console_wrapper.py  ... the PyInstaller one-file entry
#   3. release.sh    ... the model choice, artifacts, and manifest
#
# Assumptions
#   macOS: build on macOS -> .app + .dmg.
#   Linux: build on Linux -> .deb via fpm.
#   Windows: build on Windows -> .exe via PyInstaller.
#   Model weights and the Qwen3-TTS MLX server are NOT bundled. The brief
#   setup fetches them when the user picks them, or they are pre-installed by
#   the user (Ollama, llama.cpp, a downloaded voice file).
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${2:-$ROOT/releases}"
VER="${1:-1.0.0}"
mkdir -p "$OUT"

# ── 1. brief-setup.py ────────────────────────────────────────────────────
# The user-visible one-file decision-maker. Generates mindbroadcast.yaml
# after detecting every back-end this machine can run. Run it directly:
#       python3 brief-setup.py
# It is generated at the repo root by gen-release-pipeline.sh; nothing here
# regenerates it.

# ── 2. console_wrapper.py: the PyInstaller one-file entry ───────────────
# (generated in packaging/macos/console_wrapper.py; used by the macOS build)

# ── 3. brief-setup.py reporter ──────────────────────────────────────────
# Ask the user which back-end this machine came with. Nothing hidden: every
# choice is printed with its true trade-off, and the config is written to
# cwd/mindbroadcast.yaml.
if [ -f "$ROOT/brief-setup.py" ]; then
    echo ">>> brief-setup.py detected. Run it to pick your models."
    echo ">>>   python3 brief-setup.py"
else
    echo ">>> brief-setup.py not found. Run gen-release-pipeline.sh first."
fi

# ── 4. macOS .dmg ───────────────────────────────────────────────────────
# Runs on macOS. Requires PyInstaller.
if [ "$(uname)" = "Darwin" ]; then
    echo ">>> Building macOS .dmg (requires PyInstaller installed)"
    if command -v pyinstaller > /dev/null 2>&1; then
        cd "$ROOT"
        pyinstaller --noconfirm --onefile \
            --name "MindBroadcast-mac-x86_64" \
            --add-data "core/inference/console_static:$(pwd)/core/inference/console_static" \
            --hidden-import "pywebview" \
            --hidden-import "core.config" \
            --hidden-import "core.inference.lightweight" \
            packaging/macos/console_wrapper.py
        if [ -f "dist/MindBroadcast-mac-x86_64" ]; then
            mkdir -p "$OUT/MindBroadcast-mac-x86_64.app/Contents/MacOS"
            cp dist/MindBroadcast-mac-x86_64 \
                "$OUT/MindBroadcast-mac-x86_64.app/Contents/MacOS/"
            cp -r "$ROOT/packaging/macos/Info.plist" \
                "$OUT/MindBroadcast-mac-x86_64.app/Contents/"
            hdiutil create -volname "MindBroadcast" -srcfolder \
                "$OUT/MindBroadcast-mac-x86_64.app" \
                -ov -format UDZO \
                "$OUT/MindBroadcast-$VER-macos-x86_64.dmg"
            echo "   built $OUT/MindBroadcast-$VER-macos-x86_64.dmg"
        else
            echo "   Skipping macOS .dmg (PyInstaller did not produce the binary)"
        fi
    else
        echo ">>> SKIP macOS .dmg: pyinstaller not installed"
    fi
fi

# ── 5. Linux .deb ───────────────────────────────────────────────────────
# Runs on Linux. Requires fpm.
if command -v fpm > /dev/null 2>&1; then
    echo ">>> Building Linux .deb"
    cat > "$ROOT/packaging/postinst" <<'POST'
#!/usr/bin/env bash
# postinst: nothing needed here (the station is user-run).
POST
    chmod +x "$ROOT/packaging/postinst"
    cat > "$ROOT/packaging/prerm" <<'PRE'
#!/usr/bin/env bash
# prerm: nothing needed here (the daemon is user-run).
PRE
    chmod +x "$ROOT/packaging/prerm"
    cat > "$ROOT/packaging/deb-description.txt" <<'DESC'
MindBroadcast - a persistent, autonomous, news-driven broadcasting station.
Site: https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast
DESC
    fpm -s dir -t deb \
        --name mindbroadcast \
        --version "$VER" \
        --architectureamd64 \
        --description "A persistent, autonomous, news-driven broadcasting station" \
        --url "https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast" \
        --vendor "MindBroadcast" \
        --license "MIT" \
        --maintainer "Daniel Kliewer" \
        --category "science" \
        --deb-user mindbroadcast \
        --after-install packaging/postinst \
        --before-remove packaging/prerm \
        "$ROOT/core" "$ROOT/scripts" "$ROOT/docs" \
        "$ROOT/requirements.txt" \
        --description-file packaging/deb-description.txt \
        -p "$OUT" -C "$ROOT/release"
    echo "   built $OUT/mindbroadcast_${VER}_amd64.deb"
else
    echo ">>> SKIP Linux .deb: fpm not installed (sudo apt install fpm)"
fi

# ── 6. Windows .exe ─────────────────────────────────────────────────────
# Runs on Windows. PyInstaller cannot cross-compile, so this section is a
# no-op here; the user runs it on Windows with:
#   pyinstaller --onefile --name MindBroadcast-win
#   --add-data "core/inference/console_static;core/inference/console_static" \
#   packaging/macos/console_wrapper.py
if [ "$(uname)" = "Darwin" ] && command -v pyinstaller > /dev/null 2>&1; then
    echo ">>> NOT building Windows .exe here (PyInstaller cannot cross-compile)"
    echo "    Run this same script on a Windows host instead."
fi

# ── 7. publish manifest ─────────────────────────────────────────────────
cat > "$OUT/release-manifest.json" <<MANIFEST
{
  "name": "MindBroadcast",
  "version": "$VER",
  "os": { "macos": null, "linux": null, "windows": null },
  "contents": []
}
MANIFEST

for f in "$OUT"/MindBroadcast-*-macos-x86_64.dmg \
         "$OUT"/mindbroadcast_"${VER}"_amd64.deb \
         "$OUT"/MindBroadcast-*-win-*.exe; do
    [ -e "$f" ] || continue
    case "$f" in
        *macos*) MANIFEST="os.macos=$(basename "$f")";;
        *linux*) MANIFEST="os.linux=$(basename "$f")";;
        *win*)   MANIFEST="os.windows=$(basename "$f")";;
    esac
    echo "   built $f"
done

echo ">>> Release published to $OUT ($VER)"
