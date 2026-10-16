#!/usr/bin/env bash
# ── Multi-OS release pipeline for MindBroadcast ────────────────────────
# Produces (artifacts built on the host they run on):
#   releases/
#   ├── MindBroadcast-<version>-macos-<arch>.dmg     (built on macOS)
#   ├── mindbroadcast_<version>_<arch>.deb           (built on Linux, via fpm)
#   ├── MindBroadcast-<version>-win-<arch>.exe       (built on Windows, PyInstaller)
#   └── release-manifest.json
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
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${2:-$ROOT/releases}"
VER="${1:-1.0.0}"
mkdir -p "$OUT"

# Artifact names carry the real architecture. A release built on an M-series
# Mac is arm64; naming it x86_64 would hand the downloader the wrong binary.
case "$(uname -m)" in
    arm64|aarch64) ARCH="arm64" ;;
    x86_64|amd64)  ARCH="x86_64" ;;
    *)             ARCH="$(uname -m)" ;;
esac
DEB_ARCH="amd64"
[ "$ARCH" = "arm64" ] && DEB_ARCH="arm64"

# ── 1. brief-setup.py ────────────────────────────────────────────────────
# The user-visible one-file decision-maker. Generates mindbroadcast.yaml after
# detecting every back-end this machine can run. Run it directly:
#       python3 brief-setup.py
# Nothing here regenerates it; the user (or the first-run console) runs it.

# ── 2. console_wrapper.py: the PyInstaller one-file entry ───────────────
# The entry point for the desktop window. It is the same file for all three
# OS builds; only the PyInstaller --add-data separator differs.

# ── 3. brief-setup reporter ─────────────────────────────────────────────
if [ -f "$ROOT/brief-setup.py" ]; then
    echo ">>> brief-setup.py detected. Run it to pick your models:"
    echo ">>>   python3 brief-setup.py"
else
    echo ">>> brief-setup.py not found. Run gen-release-pipeline.sh first."
fi

# ── 4. macOS .dmg ───────────────────────────────────────────────────────
if [ "$(uname)" = "Darwin" ]; then
    echo ">>> Building macOS .dmg (requires PyInstaller installed)"
    if command -v pyinstaller > /dev/null 2>&1; then
        cd "$ROOT"
        APP_NAME="MindBroadcast-$ARCH"
        pyinstaller --noconfirm --onefile \
            --name "$APP_NAME" \
            --paths "$ROOT" \
            --add-data "core/inference/console_static:core/inference/console_static" \
            --hidden-import "core.inference.console" \
            --hidden-import "uvicorn.logging" \
            --hidden-import "uvicorn.loops.auto" \
            --hidden-import "uvicorn.protocols.http.auto" \
            --hidden-import "uvicorn.protocols.websockets.auto" \
            --hidden-import "uvicorn.lifespan.on" \
            --hidden-import "pywebview" \
            --hidden-import "core.config" \
            --hidden-import "core.inference.lightweight" \
            packaging/macos/console_wrapper.py
        if [ -f "dist/$APP_NAME" ]; then
            mkdir -p "$OUT/$APP_NAME.app/Contents/MacOS"
            cp "dist/$APP_NAME" "$OUT/$APP_NAME.app/Contents/MacOS/"
            cp "$ROOT/packaging/macos/Info.plist" \
                "$OUT/$APP_NAME.app/Contents/"
            # hdiutil's auto-sizing fails on this host ("No space left on
            # device" while the volume is still empty), so size the image
            # explicitly: the bundle plus generous headroom, in MB.
            APPMB=$(( $(du -sm "$OUT/$APP_NAME.app" | cut -f1) + 40 ))
            hdiutil create -volname "MindBroadcast" -srcfolder \
                "$OUT/$APP_NAME.app" \
                -ov -format UDZO -size "${APPMB}m" \
                "$OUT/MindBroadcast-$VER-macos-$ARCH.dmg"
            echo "   built $OUT/MindBroadcast-$VER-macos-$ARCH.dmg"
        else
            echo "   Skipping macOS .dmg (PyInstaller did not produce the binary)"
        fi
    else
        echo ">>> SKIP macOS .dmg: pyinstaller not installed"
    fi
fi

# ── 5. Linux .deb ───────────────────────────────────────────────────────
if [ "$(uname)" = "Linux" ]; then
    echo ">>> Building Linux .deb (requires fpm)"
    if command -v fpm > /dev/null 2>&1; then
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
        # Stage a clean copy first. fpm's --exclude did not match
        # __pycache__/*.pyc in testing, and a package must not ship .pyc
        # compiled by whatever Python ran the build. Copying the source into
        # a scratch tree (minus the junk) is deterministic.
        STAGE="$(mktemp -d)"
        trap 'rm -rf "$STAGE"' EXIT
        for item in core scripts docs requirements.txt; do
            [ -e "$ROOT/$item" ] || continue
            cp -R "$ROOT/$item" "$STAGE/"
        done
        find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
        find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true
        find "$STAGE" -name ".DS_Store" -delete 2>/dev/null || true
        echo "   staged $(find "$STAGE" -type f | wc -l | tr -d ' ') files"

        # fpm has no --description-file, and every flag must precede the first
        # path argument.
        fpm -s dir -t deb \
            --name mindbroadcast \
            --version "$VER" \
            --architecture "$DEB_ARCH" \
            --description "MindBroadcast - a persistent, autonomous, news-driven broadcasting station." \
            --url "https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast" \
            --vendor "MindBroadcast" \
            --license "MIT" \
            --maintainer "Daniel Kliewer" \
            --category "science" \
            --deb-user mindbroadcast \
            --after-install "$ROOT/packaging/postinst" \
            --before-remove "$ROOT/packaging/prerm" \
            -p "$OUT" -C "$STAGE" \
            core scripts docs requirements.txt
        echo "   built $OUT/mindbroadcast_${VER}_${DEB_ARCH}.deb"
    else
        echo ">>> SKIP Linux .deb: fpm not installed (sudo apt install fpm)"
    fi
fi

# ── 6. Windows .exe ─────────────────────────────────────────────────────
# Runs on Windows. PyInstaller cannot cross-compile, so this branch runs on a
# Windows runner where pyinstaller is installed. Git Bash reports `uname` as
# e.g. MINGW64_NT-10.0-22631, so match on the prefix rather than equality.
case "$(uname)" in
MINGW*|MSYS*|CYGWIN*|Windows_NT)
    echo ">>> Building Windows .exe (requires PyInstaller installed)"
    if command -v pyinstaller > /dev/null 2>&1; then
        # copy the Python entry to a Windows-friendly location and use ; as
        # the data separator on Windows (Git Bash).
        mkdir -p "$ROOT/packaging/windows"
        cp "$ROOT/packaging/macos/console_wrapper.py" \
            "$ROOT/packaging/windows/console_wrapper.py"
        cd "$ROOT"
        pyinstaller --noconfirm --onefile \
            --name "MindBroadcast-win-$ARCH" \
            --paths "$ROOT" \
            --add-data "core/inference/console_static;core/inference/console_static" \
            --hidden-import "core.inference.console" \
            --hidden-import "uvicorn.logging" \
            --hidden-import "uvicorn.loops.auto" \
            --hidden-import "uvicorn.protocols.http.auto" \
            --hidden-import "uvicorn.protocols.websockets.auto" \
            --hidden-import "uvicorn.lifespan.on" \
            --hidden-import "pywebview" \
            --hidden-import "core.config" \
            --hidden-import "core.inference.lightweight" \
            packaging/windows/console_wrapper.py
        if [ -f "dist/MindBroadcast-win-$ARCH.exe" ]; then
            cp "dist/MindBroadcast-win-$ARCH.exe" "$OUT/MindBroadcast-$VER-win-$ARCH.exe"
            echo "   built $OUT/MindBroadcast-$VER-win-$ARCH.exe"
        else
            echo "   Skipping Windows .exe (PyInstaller did not produce the binary)"
        fi
    else
        echo ">>> SKIP Windows .exe: pyinstaller not installed"
    fi
    ;;
esac

# ── 7. publish manifest ─────────────────────────────────────────────────
# Collect whatever this host actually produced and write it as real JSON.
MACOS_ART=""; LINUX_ART=""; WIN_ART=""
for f in "$OUT"/MindBroadcast-*-macos-*.dmg; do
    [ -e "$f" ] || continue; MACOS_ART="$(basename "$f")"
done
for f in "$OUT"/mindbroadcast_"${VER}"_*.deb; do
    [ -e "$f" ] || continue; LINUX_ART="$(basename "$f")"
done
for f in "$OUT"/MindBroadcast-*-win-*.exe; do
    [ -e "$f" ] || continue; WIN_ART="$(basename "$f")"
done

json_str() { [ -n "$1" ] && printf '"%s"' "$1" || printf 'null'; }

cat > "$OUT/release-manifest.json" <<MANIFEST
{
  "name": "MindBroadcast",
  "version": "$VER",
  "arch": "$ARCH",
  "os": {
    "macos": $(json_str "$MACOS_ART"),
    "linux": $(json_str "$LINUX_ART"),
    "windows": $(json_str "$WIN_ART")
  }
}
MANIFEST

for a in "$MACOS_ART" "$LINUX_ART" "$WIN_ART"; do
    [ -n "$a" ] && echo "   artifact: $a"
done

# Fail closed. A toolchain that is missing produces no artifact, and a
# release job that goes green with nothing attached is worse than a red one:
# the earlier 'pyinstaller not installed' path exited 0 and published an
# empty release. If this host was expected to build something and did not,
# exit non-zero.
if [ -z "$MACOS_ART" ] && [ -z "$LINUX_ART" ] && [ -z "$WIN_ART" ]; then
    echo "ERROR: no artifact was produced on $(uname) (arch $ARCH)." >&2
    echo "       Check that the toolchain for this OS is installed" >&2
    echo "       (pyinstaller on macOS/Windows, fpm on Linux)." >&2
    exit 1
fi

echo ">>> Release published to $OUT ($VER, $ARCH)"
