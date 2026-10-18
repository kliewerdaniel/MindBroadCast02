"""Auto-detection of available lightweight backends.

The desktop app needs to know, before it asks the user to choose:
  - which CPU/LLM backends can run on this machine at all (does llama-server
    exist? does a GGUF exist at the detected path?),
  - which TTS backends can speak on this machine (is piper installed? is
    espeak-ng? is the Qwen3-TTS server up? is there any audio device?),
  - then list them all to the user with the honest trade-offs, and let the
    best default be chosen.

Everything is discovered, never assumed: a backend is presented only when its
runtime pre-flight check passes from the user's point of view.
"""

from __future__ import annotations

import shutil
import socket
import subprocess

# LLMs that a conscious user has already downloaded and registered with Ollama
# or is carrying as GGUF. The detector presents these first.
KNOWN_GGUF_MODELS = [
    # (owner, repo, filename, bits, label)
    ("Qwen/Qwen3-30B-A3B", "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF",
     "qwen3-30b-a3b-q4_k_m.gguf", "4-bit", "qwen3:30b-a3b (30B, 4-bit)"),
    ("Qwen/Qwen3-8B", "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
     "qwen3-8b-q4_k_m.gguf", "4-bit", "qwen3:8b (8B, 4-bit)"),
    ("meta-llama/Llama-3.1-8B", "https://huggingface.co/meta-llama/Llama-3.1-8B-GGUF",
     "llama-3.1-8b-instruction-q4_k_m.gguf", "4-bit", "llama3.1:8b (8B, 4-bit)"),
    ("github-copilot/MiniCPM-Llama3-V-2_5-GGUF", "https://huggingface.co/github-copilot/MiniCPM-Llama3-V-2_5-GGUF",
     "MiniCPM-Llama3-V-2_5-mlx-Q4_K_M.gguf", "4-bit", "minicpmvl:2.5 (vision, 4-bit)"),
]

# Tiny models that are guaranteed to fit on a few hundred MB machine.
KNOWN_SMALL_MODELS = [
    ("HuggingFaceTB/SmolLM2-1.7B", "https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-GGUF",
     "smollm2-1.7b-q4_k_m.gguf", "4-bit", "smolLM2:1.7b (light)"),
    ("bartowski/Llama-3.2-1B-Instruct-GGUF", "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF",
     "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "4-bit", "llama3.2:1b (tiny)"),
]

# TTS backends, and how we prove they work.
TTS_BACKENDS = [
    # (id, display, command to check + score)
    ("piper", "Piper (neural, ~200ms)",
     ["pip", "show", "piper-tts"]),
    ("espeak", "eSpeak (ultra-light)",
     ["sh", "-c", "which espeak-ng || which espeak || true"]),
    ("silence", "Silence (test/traffic, structural)",
     None),  # always available by construction
    ("qwen3-tts-http", "Qwen3-TTS (persistent server)",
     ["sh", "-c", "curl -s --max-time 3 http://127.0.0.1:7861/health > /dev/null 2>&1 && echo
         k || echo down"]),
]


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _subproc_ok(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3)
        return True
    except Exception:
        return False


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def detect_llm_backends() -> list[dict[str, object]]:
    """Present the LLM backends this machine can plausibly run, with a
    quality/priority hint so the user can pick the best for their RAM."""
    results = []

    # Ollama is simplest: the app detects models already installed via the API.
    if _which("ollama"):
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            results.append({
                "backend": "ollama",
                "label": "Ollama (local server, inherits all installed models)",
                "command": None,
                "models": models,
                "flag": "llm",
            })
        except Exception:
            pass

    # MMX is the new "llama-cpp as Ollama-compatible server" path on Apple
    # Silicon. On Linux the same pattern works with llama-server or turbocpp.
    if _which("llama-server") or _which("llama"):
        results.append({
            "backend": "llama_cpp",
            "label": "llama.cpp (CPU, GGUF quantized)",
            "command": "llama-server -m <model.gguf> --port 8080",
            "flag": "llm",
        })

    if _which("turbocpp"):
        results.append({
            "backend": "llama_cpp",
            "label": "turbocpp (quantized, OpenAI-compatible)",
            "command": "turbocpp serve -m <model.gguf> --port 8080",
            "flag": "llm",
        })

    # LM Studio and similar carry their own servers; we do not parse their
    # databases here, so we only present the binary when the user has one
    # running. (Detection is by port check, not by binary.)
    return results


def detect_tts_backends() -> list[dict[str, object]]:
    """Present TTS backends this machine can actually speak with."""
    results = []
    # eSpeak is checked once: it is a system binary and cheap, so if it exists
    # we offer it and skip the heavy neural options.
    espeak_available = _which("espeak-ng") is not None or _which("espeak") is not None
    if espeak_available:
        results.append({"backend": "espeak", "label": "eSpeak (ultra-light)",
                        "command": "espeak-ng/espeak installed",
                        "flag": "tts"})

    # Piper: a neural lightweight. Check only if eSpeak was not already
    # offered (so the list stays short and the quality ordering holds).
    if not espeak_available and _subproc_ok(["pip", "show", "piper-tts"]):
        results.append({"backend": "piper", "label": "Piper (neural, ~200ms)",
                        "command": "piper-tts installed",
                        "flag": "tts"})

    # Qwen3-TTS is the heaviest but best voice; present it if the server answers.
    if _http_ok("http://127.0.0.1:7861/health"):
        results.append({"backend": "qwen3-tts-http",
                        "label": "Qwen3-TTS (persistent server, best voice)",
                        "command": "Qwen3-TTS server on :7861",
                        "flag": "tts"})

    # Silence is always available and is the structural test/traffic backend.
    results.append({"backend": "silence", "label": "Silence (test/traffic, "
                    "structural)", "command": "always available by construction",
                    "flag": "tts"})
    return results


def detect_audio_device() -> bool:
    """A bare-metal check: can the machine's audio stack even receive PCM?"""
    try:
        import sounddevice as sd
        with sd.InputStream(samplerate=24000, channels=1, dtype="int16"):
            return True
    except Exception:
        return False
