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

def _project_root() -> Path:
    """Where the app's data/ directory lives, and the cwd for spawned servers.

    Source layout: this file is ``<root>/core/desktop.py``, so the project root
    is ``parent.parent``. (``parent.parent.parent`` is one level too high and
    made every spawned server start outside the tree, where ``import core``
    fails with ModuleNotFoundError.)

    Frozen one-file builds extract to a temp dir, so ``__file__`` cannot locate
    the user's data. There the writable location is the user's home — a .app
    inside a read-only DMG cannot write next to itself anyway.
    """
    if getattr(sys, "frozen", False):
        base = Path.home() / ".mindbroadcast"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).resolve().parent.parent


_ROOT = _project_root()
(_ROOT / "data").mkdir(parents=True, exist_ok=True)
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


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _start_tts() -> subprocess.Popen:
    """Start the persistent TTS server with the venv's own interpreter.

    In a frozen bundle ``sys.executable`` is the app binary itself, so spawning
    ``[sys.executable, "-m", ...]`` would relaunch the whole app recursively
    instead of starting a server. There we run the server in a thread instead
    (see ``_serve_in_thread``).
    """
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


def _serve_in_thread(target, *args, **kwargs) -> None:
    """Run a blocking server in a daemon thread (frozen builds only).

    A frozen one-file app has no interpreter on PATH to spawn, so the servers
    must live in this process. Daemon threads die with the app.
    """
    import threading

    def _run():
        try:
            target(*args, **kwargs)
        except Exception as exc:  # a dead server must not kill the window
            try:
                (_ROOT / "data" / "server-error.log").write_text(repr(exc))
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True).start()


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
    frozen = _frozen()

    # 1. TTS server.
    if not _healthy(f"http://127.0.0.1:{_TTS_PORT}/health"):
        if frozen:
            # No interpreter to spawn: run it here. A frozen release also has
            # no MLX/Qwen3-TTS model, so this is expected to stay unhealthy —
            # the console reports tts:false and the station runs silent rather
            # than blocking the window on a server that cannot start.
            from core.inference.tts_server import main as _tts_main
            _serve_in_thread(_tts_main, ["--port", str(_TTS_PORT)])
        else:
            started.append(_start_tts())
        # Do NOT block the window on TTS: speech is optional, the station is
        # not. A missing voice must never leave the user with no UI.
        if not frozen and not _wait_for(f"http://127.0.0.1:{_TTS_PORT}/health", 20):
            (_ROOT / "data").mkdir(exist_ok=True)
            (_ROOT / "data" / "tts-unavailable.log").write_text(
                _log_tail("tts.log") or "(no log)")

    # 2. Console.
    if not _healthy(f"http://127.0.0.1:{_CONSOLE_PORT}/api/health"):
        if frozen:
            from core.inference.console import app as _console_app
            import uvicorn as _uvicorn
            _serve_in_thread(
                _uvicorn.run, _console_app,
                host="127.0.0.1", port=_CONSOLE_PORT, log_level="warning")
        else:
            started.append(_start_console())
        if not _wait_for(f"http://127.0.0.1:{_CONSOLE_PORT}/api/health", 60):
            webview.create_window(
                "MindBroadcast",
                html=_error_page(
                    "The broadcast console did not start.",
                    "Last lines of data/console.log:",
                    _log_tail("console.log")))
            webview.start()
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
