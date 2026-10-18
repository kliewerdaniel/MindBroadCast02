# Deployment

On macOS, MindBroadcast ships as a native application. The source is a single
virtualenv install; the desktop bundle is a thin launcher over the same
project.

## The macOS app

```text
build/MindBroadcast.app/
├── Contents/
│   ├── Info.plist                     # bundle ID com.kliewerdaniel.mindbroadcast
│   ├── MacOS/
│   │   └── MindBroadcast              # bash launcher -> python -m core.desktop
│   └── Resources/
│       ├── icon.icns                  # 10-slice brand orb
│       └── project_root               # recorded project path (fallback if moved)
```

**Run it:**

```bash
./packaging/macos/build.sh --run
open build/MindBroadcast.app
```

The bundle is unsigned. The first launch needs a right-click → Open:

```bash
xattr -dr com.apple.quarantine build/MindBroadcast.app
```

**What the app does**

`core/desktop.py` is a thin shell. It resolves the project root from the bundle's
location, starts the Qwen3-TTS server (`core.inference.tts_server`) and the
console server (`core.inference.console`) with the project's virtualenv if they
are not already running, and opens a `pywebview` window on the console. All
editorial, epistemic, and broadcast logic lives in the console/runtime modules;
a defect in the broadcast is never "the desktop app's problem". It also never
manages Ollama's lifecycle (ADR: report a dead endpoint and wait).

| Server it owns | Server it reports |
|---|---|
| Qwen3-TTS on :7861 | Ollama (LLM) — never started, never killed |
| Console on :7862 | — |
| `pywebview` window | — |

Closing the window stops only the servers the app started; a server you started
yourself is left running. The app is localhost-only, consistent with the
fail-closed rule for inference endpoints.

## Building the bundle from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./packaging/macos/build.sh --run
```

The launcher resolves the project root dynamically, so a relocated `.app`
still finds the project.

## Signing and notarization

Out of scope for this build and intentionally left unsigned so a rebuild always
reflects current code:

- The app is unsigned. The first launch needs right-click → Open (or
  `xattr -dr com.apple.quarantine`).
- Production signing + notarization is a separate, human-level decision
  (developer ID, keychain, Apple Developer account). Teach `build.sh` once the
  signing identity is chosen; do not bake secrets into the bundle.

## Linux

A native app is not yet packaged for Linux. The deployable surface there is the
console and the TTS server:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn core.inference.console:app --host 127.0.0.1 --port 7862
# TTS server on :7861
python3 -m core.inference.tts_server --port 7861
```

Run the console in a terminal, or wrap the same command with `pywebview`
(`pywebview.create_window("MindBroadcast", "http://127.0.0.1:7862")` + `webview.start()`)
if you want a system-window shell.

## Docker

No production Dockerfile is shipped yet. The container would run the TTS server
and the console (both are fixed, stateless Python processes); the LLM and TTS
model weights are fetched by Ollama and Qwen3-TTS at first use, not bundled.

## Linux placement (this machine)

```bash
# 1. python3.13 venv + the full stack
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. models (local, fail-closed)
ollama pull qwen3:30b-a3b
ollama pull nomic-embed-text

# 3. console + tts servers
.venv/bin/python -m core.inference.tts_server --port 7861
.venv/bin/python -m uvicorn core.inference.console:app --host 127.0.0.1 --port 7862
```

## What users configure (what they never touch)

- Sources and regions (25 feeds / 8 regions in `core/inference/world_sources.py`)
- Persona traits and voices (`core/inference/voices.py`, `core/personas/`)
- TTS speed and voice profile
- Broadcast interval and model endpoint

The console, compiler, persistence, and all epistemic logic are never modified by
the user.

## Maintainers: adding a new platform app

1. Write the launcher shell (`main` of `core/desktop.py`, same `python -m` entry
   the existing `build.sh` calls).
2. Add the bundle `Info.plist` (bundle ID, `LSMinimumSystemVersion`,
   `NSAppTransportSecurity` for localhost feeding, icon).
3. Rebuild with `packaging/macos/build.sh` (or a copy of that script for the
   platform) and verify the window opens + the console loads.
4. Update this document and `README.md` with the platform-specific run steps.
