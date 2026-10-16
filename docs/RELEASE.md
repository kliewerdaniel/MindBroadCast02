# Release Guide

How a MindBroadcast release is produced and published.

## The model

Every artifact is built **on the OS it runs on**. PyInstaller cannot
cross-compile, so a Linux `.deb` cannot be produced from macOS, and a macOS
`.dmg` cannot be produced from Linux. GitHub Actions runs one builder per OS,
in parallel, on a version tag.

| Artifact | Builder | Runner | Tool |
|---|---|---|---|
| macOS `.dmg` | `packaging/release.sh` (Darwin branch) | `macos-latest` | PyInstaller + `hdiutil` |
| Linux `.deb` | `packaging/release.sh` (Linux branch) | `ubuntu-latest` (Debian container) | `fpm` |
| Windows `.exe` | `packaging/release.sh` (Windows branch) | `windows-latest` | PyInstaller |

## Cut a release

1. **The tree must be green.** `ci.yml` runs on every push; do not tag a red
   commit. Locally:

   ```
   .venv/bin/python -m pytest tests/ -q
   ```

2. **Tag and push the tag.** The tag is what triggers the builders.

   ```
   git tag -a v1.2.0 -m "MindBroadcast v1.2.0"
   git push origin v1.2.0
   ```

3. **Watch the three workflows.** Each attaches its artifact to the GitHub
   Release for that tag:

   - `.github/workflows/build-linux.yml` → `Brief Setup (Linux)` → `.deb`
   - `.github/workflows/build-os.yml` → `Brief Setup (Windows)` → `.exe`
   - the macOS `.dmg` job in the same workflow set → `macos-latest`

   The `check` job in each workflow is a zero-dependency base job. GitHub
   rejects a workflow whose every job has `needs`, so that job always runs and
   keeps the file valid on a plain branch push.

4. **Publish the Release.** The release builders upload artifacts; attach them
   to the release notes and mark the release published.

## What is never bundled

Model weights and the Qwen3-TTS MLX server are **not** in any artifact. They
are large (the reference LLM is ~17GB) and machine-specific. The first user
runs `brief-setup.py`, which:

- probes this machine for LLM backends (`ollama`, `llama.cpp`/GGUF) and TTS
  backends (`qwen3-tts-http`, `piper`, `espeak`, `silence`),
- presents only the ones whose pre-flight check passes (fail-closed — it never
  invents a default that cannot run),
- writes `mindbroadcast.yaml`.

Nothing is fetched at runtime. If the user has no usable backend, setup says so
instead of pretending otherwise.

## Local dry run

You can build the artifact for your own OS without CI:

```
./packaging/release.sh 1.2.0 ./releases
```

It writes `releases/` and a `release-manifest.json`. Missing toolchains are
reported and skipped, not faked:

- macOS `.dmg` needs `pyinstaller` and uses `hdiutil` (system).
- Linux `.deb` needs `fpm` (`gem install fpm`).
- Windows `.exe` needs `pyinstaller`.

## macOS first launch

The app is unsigned. The first launch needs a right-click → **Open**, or:

```
xattr -dr com.apple.quarantine /Applications/MindBroadcast.app
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for the per-platform install walkthrough.
