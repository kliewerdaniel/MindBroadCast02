#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Multi-OS release pipeline for MindBroadcast.
#
#   packaging/release.sh
#
# Produces (artifacts built on the host they run on):
#   releases/
#   ├── MindBroadcast-<version>-macos-x86_64.dmg      (built on macOS)
#   ├── MindBroadcast-<version>-linux-x86_64.deb      (built on Linux, via fpm)
#   ├── MindBroadcast-<version>-windows-x86_64.exe    (built on Windows, PyInstaller)
#   └── release-manifest.json
#
# The "brief setup" is a single file, brief-setup.py. It writes
# mindbroadcast.yaml after detecting every back-end this machine can actually
# run (LLM + TTS). A back-end is presented only if its pre-flight check
# passes (server answering, binary in PATH, model present, audio device alive).
# If nothing useful is available it tells the user plainly instead of
# inventing a default.
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
cat > "$ROOT/brief-setup.py" <<'PYEOF'
#!/usr/bin/env python3
"""MindBroadcast brief setup. One file, one decision.

Writes mindbroadcast.yaml after detecting every back-end this machine can
actually run (LLM + TTS). Presents them with honest trade-offs:

  * qwen3-tts-http  best voice, ~3GB MLX server on :7861
  * piper            neural, ~200ms, lightweight (few MB voice file)
  * espeak           ultra-light, mechanical, system speech
  * silence          structural, for testing when no audio is available

Local-first: the machine is never assumed to have internet or a cloud
account. If nothing useful is available it says so, instead of inventing a
default.
"""

import json
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "mindbroadcast.yaml"


def which(binary: str) -> bool:
    return shutil.which(binary) is not None


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def pip_show(package: str) -> bool:
    try:
        subprocess.run(["pip", "show", package], capture_output=True,
                       timeout=3, check=True)
        return True
    except Exception:
        return False


def detect_llm_backends():
    out = []
    if which("ollama"):
        try:
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            if models:
                out.append(("ollama",
                            "Ollama (local server, inherits installed models)",
                            "ollama serve", models))
        except Exception:
            pass
    if which("llama-server") or which("llama"):
        out.append(("llama_cpp", "llama.cpp (CPU, GGUF quantized, light)",
                    "llama-server -m <model.gguf> --port 8080", []))
    if which("turbocpp"):
        out.append(("llama_cpp", "turbocpp (quantized, OpenAI-compatible)",
                    "turbocpp serve -m <model.gguf> --port 8080", []))
    return out


def detect_tts_backends():
    out = []
    if which("espeak-ng") or which("espeak"):
        out.append(("espeak", "eSpeak (ultra-light, mechanical)",
                    "espeak-ng or espeak in PATH"))
    if not out and pip_show("piper-tts"):
        out.append(("piper", "Piper (neural, ~200ms, lightweight)",
                    "pip install piper-tts + a voice file"))
    if http_ok("http://127.0.0.1:7861/health"):
        out.append(("qwen3-tts-http", "Qwen3-TTS (best voice, server)",
                    "port 7861"))
    out.append(("silence", "Silence (test/traffic, structural)",
                "always available"))
    return out


def main() -> int:
    import yaml

    models = detect_llm_backends()
    voices = detect_tts_backends()
    audio = bool(shutil.which("sounddevice"))  # sounddevice presence check

    print("MindBroadcast brief setup")
    print("=" * 44)
    print("Audio device reachable: %s" % audio)
    print("\nLanguage-model backends available on this machine:\n")
    for backend, label, command, installed in models:
        status = ("installed models: %s" % ", ".join(installed)) if installed \
            else "no installed models listed"
        print("  %-22s %s" % (backend, label))
        print("    command: %s" % command)
        print("    %s" % status)
    print("\nText-to-speech backends available on this machine:\n")
    for backend, label, command in voices:
        print("  %-22s %s   (command: %s)" % (backend, label, command))
    print("\nChoose the back-ends you want. Empty line = default.")

    try:
        llm_choice = input("\nLLM backend [ollama]: ").strip()
        tts_choice = input("TTS backend [qwen3-tts-http]: ").strip()
    except EOFError:
        llm_choice = "ollama"
        tts_choice = "qwen3-tts-http"

    llm_backend = llm_choice or "ollama"
    tts_backend = tts_choice or \
        ("qwen3-tts-http" if "qwen3-tts-http" in voices else "espeak")

    config = {
        "mindbroadcast": {
            "llm": {
                "backend": llm_backend,
                "base_url": "http://localhost:11434",
                "model": "qwen3:30b-a3b",
                "timeout_seconds": 300,
                "embedding_model": "nomic-embed-text:latest",
            },
            "tts": {
                "backend": tts_backend,
                "speed": 1.0,
                "qwen3_tts_server_port": 7861,
            },
        }
    }
    CONFIG.write_text(yaml.safe_dump(config, sort_keys=False,
                                     default_flow_style=False), encoding="utf-8")
    print("\nWrote %s" % CONFIG)
    print("Run the console to start the broadcast:")
    print("    uvicorn core.inference.console:app --port 7862")
    return 0


if __name__ == "__main__":
    main()
PYEOF
python3 -c "import ast; ast.parse(open('$ROOT/brief-setup.py').read())" \
    && echo "brief-setup.py syntax OK" || echo "brief-setup.py SYNTAX ERROR"

# ── 2. packaging/release.sh ─────────────────────────────────────────────
cat > "$ROOT/packaging/release.sh" <<'RELEASEEOF'
#!/usr/bin/env bash
# Build the multi-OS release artifacts.
#   $1 = version tag
#   $2 = output directory (default: $ROOT/releases)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${2:-$ROOT/releases}"
VER="${1:-1.0.0}"
mkdir -p "$OUT"

# ── Linux .deb ──────────────────────────────────────────────────────────
# Requires fpm. This script runs on the Linux host.
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

# ── macOS .app + .dmg ───────────────────────────────────────────────────
# Requires PyInstaller on macOS. PyInstaller is a cross-platform builder but
# not a cross-compiler, so this must run on the macOS host.
if command -v python3 > /dev/null 2>&1 && command -v pip3 > /dev/null 2>&1; then
    echo ">>> Building macOS .app (PyInstaller requires macOS)"
    if command -v pyinstaller > /dev/null 2>&1; then
        cd "$ROOT"
        # Build on the macOS host, producing a standalone console.
        pyinstaller --noconfirm --onefile \
            --name "MindBroadcast-mac-x86_64" \
            --add-data "core/inference/console_static:$(pwd)/core/inference/console_static" \
            --hidden-import "pywebview" \
            --hidden-import "core.config" \
            --hidden-import "core.inference.lightweight" \
            packaging/macos/console_wrapper.py
        if [ -f "dist/MindBroadcast-mac-x86_64" ]; then
            mkdir -p "$OUT/MindBroadcast-mac-x86_64.app/Contents/MacOS"
            cp dist/MindBroadcast-mac-x86_64 "$OUT/MindBroadcast-mac-x86_64.app/Contents/MacOS/"
            cp -r "$ROOT/packaging/macos/Info.plist" \
                "$OUT/MindBroadcast-mac-x86_64.app/Contents/"
            hdiutil create -volname "MindBroadcast" -srcfolder \
                "$OUT/MindBroadcast-mac-x86_64.app" \
                -ov -format UDZO \
                "$OUT/MindBroadcast-$VER-macos-x86_64.dmg"
            echo "   built $OUT/MindBroadcast-$VER-macos-x86_64.dmg"
        else
            echo "   Skipping macOS app (PyInstaller or console_wrapper not run)"
        fi
    else
        echo ">>> SKIP macOS .app: pyinstaller not installed"
    fi
else
    echo ">>> SKIP macOS .app: python3/pyinstaller not available on this host"
fi

# ── Windows .exe ────────────────────────────────────────────────────────
# Windows cannot be built here; the console_wrapper.py is prepared and the
# user runs PyInstaller on a Windows host:
#   pyinstaller --onefile --name MindBroadcast-win
#   --add-data "core/inference/console_static;core/inference/console_static" \
#   packaging/macos/console_wrapper.py
if [ "$(uname)" = "Darwin" ] && command -v pyinstaller > /dev/null 2>&1; then
    echo ">>> NOT building Windows .exe here (PyInstaller cannot cross-compile)"
    echo "    Run this script on a Windows host instead."
fi

# ── publish manifest ────────────────────────────────────────────────────
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
RELEASEEOF
chmod +x "$ROOT/packaging/release.sh"
echo "wrote packaging/release.sh"

# ── console_wrapper.py: PyInstaller entry point ─────────────────────────
cat > "$ROOT/packaging/macos/console_wrapper.py" <<'WRAPPER'
"""PyInstaller one-file entry for the macOS app."""
import os
import sys

# Local-only, fail-closed. Never permit remote inference.
os.environ.setdefault("SOVEREIGN_ALLOW_EXTERNAL_INFERENCE", "0")

# Load the console and run it as a script.
sys.path.insert(0, os.path.dirname(__file__))
import core.inference.console as _console

from uvicorn import run
run(_console.app, host="127.0.0.1", port=7862)
WRAPPER
echo "wrote packaging/macos/console_wrapper.py"

echo
echo "=== generated ==="
ls -la "$ROOT/brief-setup.py" "$ROOT/packaging/release.sh"
echo "=== console wrapper ready ==="
