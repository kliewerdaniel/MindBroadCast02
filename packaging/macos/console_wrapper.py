"""PyInstaller entry point for the MindBroadcast desktop app.

This is the frozen counterpart of `python -m core.desktop`: it opens the native
window and starts the servers behind it. The console is a FastAPI app served on
127.0.0.1:7862, localhost-only by the same fail-closed rule as every inference
endpoint.

Frozen builds are self-contained: the `core` package is bundled by PyInstaller
(see packaging/release.sh, which passes --paths for the project root and lists
the needed modules as hidden imports). In a frozen bundle `__file__` points
inside the temporary extraction dir, so we must NOT prepend that dir to
sys.path — PyInstaller's own loader already resolves `core` from the archive.

An earlier revision of this file only called `uvicorn.run()`. That made the
.app a headless server with no window: it launched, served HTTP, and macOS
reported the app as "not responding" because no Cocoa event loop ever started.
The window is the product; the server is an implementation detail behind it.
"""

import os
import sys
import traceback

# Local-only, fail-closed. Never let a misconfiguration become a covert cloud
# call. This guard matches the rule already in every provider.
os.environ.setdefault("SOVEREIGN_ALLOW_EXTERNAL_INFERENCE", "0")

# When run from source (not frozen), make the project root importable so
# `import core...` resolves. In a frozen bundle the package is already on the
# loader's path, and the extraction dir must not be inserted.
if not getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)


def _log_error() -> None:
    """Write the traceback to ~/.mindbroadcast/data/launch-error.log."""
    _log = os.path.join(os.path.expanduser("~"), ".mindbroadcast", "data")
    os.makedirs(_log, exist_ok=True)
    with open(os.path.join(_log, "launch-error.log"), "w") as f:
        f.write(traceback.format_exc())


try:
    from core.desktop import main
except Exception:
    _log_error()
    raise

if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log_error()
        raise
