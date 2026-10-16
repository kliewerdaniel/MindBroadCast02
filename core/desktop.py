"""The desktop app: a native window around the broadcast console.

Run the station as a macOS application instead of a browser tab:

    .venv/bin/python -m core.desktop

Opens a native window (WebKit via pywebview) pointed at the console, and starts
both servers behind it if they are not already running:

  1. the Qwen3-TTS server (:7861)
  2. the FastAPI console (:7862)

Design boundaries:

- The app is a **shell**, not a reimplementation. All editorial, epistemic, and
  broadcast logic stays in the console/runtime modules; this file only starts
  processes and shows a window. A defect in the broadcast is never "the desktop
  app's problem".
- **Fail closed.** If a server does not come up, the window shows the reason
  rather than a blank page. It never fakes a working broadcast.
- **It does not manage the model server.** Ollama is the user's process (ADR:
  never launch or kill it). The app only reports whether the LLM is reachable.
- Closing the window stops the servers this process started. Servers the user
  started themselves are left running.
"""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_TTS_PORT = 7861
_CONSOLE_PORT = 7862
_CONSOLE_URL = f"http://127.0.0.1:{_CONSOLE_PORT}/"


def _healthy(url: str, timeout: float = 3.0) -> bool:
    """Whether an endpoint responds. A false can also mean 'not started yet'."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            import json
            return bool(json.loads(resp.read().decode()).get("ok", resp.status == 200))
    except Exception:
        return False


def _start_tts() -> subprocess.Popen:
    """Start the persistent TTS server with the venv's own interpreter."""
    log = open(_ROOT / "data" / "tts.log", "w")
    return subprocess.Popen(
        [sys.executable, "-m", "core.inference.tts_server",
         "--port", str(_TTS_PORT)],
        cwd=str(_ROOT), stdout=log, stderr=subprocess.STDOUT)


def _start_console() -> subprocess.Popen:
    """Start the console with uvicorn, quiet (the window IS the output)."""
    log = open(_ROOT / "data" / "console.log", "w")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "core.inference.console:app",
         "--host", "127.0.0.1", "--port", str(_CONSOLE_PORT)],
        cwd=str(_ROOT), stdout=log, stderr=subprocess.STDOUT)


def _log_tail(name: str) -> str:
    """Last lines of a child's log, for the error page."""
    try:
        text = (_ROOT / "data" / name).read_text(errors="replace")
        return "\n".join(text.strip().splitlines()[-6:]) or "(log empty)"
    except OSError:
        return "(no log)"


def _wait_for(url: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _healthy(url):
            return True
        time.sleep(0.5)
    return False


def _build_app() -> None:
    """Open the native window, starting whatever is not already up."""
    import webview

    started: list = []

    # 1. TTS server.
    if not _healthy(f"http://127.0.0.1:{_TTS_PORT}/health"):
        started.append(_start_tts())
        if not _wait_for(f"http://127.0.0.1:{_TTS_PORT}/health", 120):
            webview.create_window(
                "MindBroadcast",
                html=_error_page(
                    "The speech server did not start.",
                    "Last lines of data/tts.log:",
                    _log_tail("tts.log")))
            return

    # 2. Console.
    if not _healthy(f"http://127.0.0.1:{_CONSOLE_PORT}/api/health"):
        started.append(_start_console())
        if not _wait_for(f"http://127.0.0.1:{_CONSOLE_PORT}/api/health", 60):
            webview.create_window(
                "MindBroadcast",
                html=_error_page(
                    "The broadcast console did not start.",
                    "Last lines of data/console.log:",
                    _log_tail("console.log")))
            return

    # 3. Show it.
    window = webview.create_window(
        "MindBroadcast — a station that watches the world",
        _CONSOLE_URL,
        width=1500, height=1000,
        min_size=(900, 600),
        background_color="#05060A",
    )

    def _on_closed():
        # Stop only what we started; leave a user-started server alone.
        for proc in started:
            try:
                proc.terminate()
            except Exception:
                pass

    if window is not None:
        window.events.closed += _on_closed
    webview.start()


def _error_page(title: str, detail: str, log: str = "") -> str:
    log_block = (f'<pre>{log}</pre>' if log else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body {{ background:#05060a; color:#e8ecf8; font-family:system-ui,sans-serif;
            display:flex; align-items:center; justify-content:center; height:100vh;
            margin:0; }}
    .box {{ max-width:560px; padding:32px; border:1px solid rgba(120,160,255,.18);
            border-radius:14px; background:rgba(14,17,28,.7); }}
    h1 {{ color:#ff5e8a; font-size:17px; letter-spacing:2px; margin:0 0 12px; }}
    p {{ color:#8b93ad; font-size:14px; line-height:1.5; }}
    pre {{ color:#4a5170; font-size:11px; line-height:1.4; margin-top:14px;
           padding:12px; background:#05060a; border-radius:8px; overflow-x:auto; }}
    </style></head><body><div class="box">
    <h1>{title}</h1><p>{detail}</p>{log_block}</div></body></html>"""


def main() -> None:
    # The app is localhost-only by the same fail-closed rule as the servers.
    os.environ.setdefault("SOVEREIGN_ALLOW_EXTERNAL_INFERENCE", "0")
    _build_app()


if __name__ == "__main__":
    main()
