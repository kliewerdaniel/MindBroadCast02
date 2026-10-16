"""PyInstaller one-file entry point for the MindBroadcast console.

The desktop window points at http://127.0.0.1:7862, where this module's FastAPI
app serves the console and its SSE transcript + audio endpoints. The app is
localhost-only, by the same fail-closed rule as every inference endpoint.

Run it on every platform the PyInstaller `--onefile` build lands on; only the
--add-data separator differs (':' on macOS/Linux, ';' on Windows).
"""

import os
import sys

# Local-only, fail-closed. Never let a misconfiguration become a covert cloud
# call. This guard matches the rule already in every provider.
os.environ.setdefault("SOVEREIGN_ALLOW_EXTERNAL_INFERENCE", "0")

# Load the console module and run it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core.inference.console as _console

from uvicorn import run

run(_console.app, host="127.0.0.1", port=7862)
