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
    """Return [(backend_id, label, command, installed_models)]."""
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
    audio = bool(shutil.which("sounddevice"))

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
