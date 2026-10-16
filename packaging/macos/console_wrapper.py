"""PyInstaller one-file entry point for the MindBroadcast console.

The desktop window points at http://127.0.0.1:7862, where this module's FastAPI
app serves the console and its SSE transcript + audio endpoints. The app is
localhost-only, by the same fail-closed rule as every inference endpoint.

Run it on every platform the PyInstaller `--onefile` build lands on; only the
--add-data separator differs (':' on macOS/Linux, ';' on Windows).

Frozen builds are self-contained: the `core` package is bundled by PyInstaller
(see packaging/release.sh, which passes --paths for the project root and lists
the console as a hidden import). In a frozen bundle `__file__` points inside
the temporary extraction dir, so we must NOT prepend that dir to sys.path —
PyInstaller's own loader already resolves `core` from the archive.
"""

import os
import sys

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

from uvicorn import run

import core.inference.console as _console


if __name__ == "__main__":
    run(_console.app, host="127.0.0.1", port=7862)
