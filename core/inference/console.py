"""The MindBroadcast listener console.

A minimal web player over the live runtime: open localhost in a browser to hear
the broadcast and watch the transcript arrive. FastAPI + vanilla JS, SSE for the
transcript, per-segment audio served for playback — the dependency-light pattern
from the pipeline-web-ui discipline.

    uvicorn core.inference.console:app --port 7862

The console owns a LiveRuntime (reusing the proven real LLM + TTS-server path),
retains each segment's audio to disk so the browser can fetch and play it in
order, and streams transcript lines over SSE as they air.

Design boundaries preserved:
- §13.4: generated ≠ delivered. Only segments that completed playback appear in
  the public transcript; the audio endpoint serves what was actually synthesized.
- §16.8: audio (HTTP file) and metadata (SSE) are separate channels.
- Fail-closed: if the LLM or TTS is down, /health says so and the run reports it
  rather than fabricating.
"""

import asyncio
import json
import os
import struct
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..broadcast.providers import ProviderError
from ..inference.autonomous import AutonomousBroadcaster
from ..inference.providers import OllamaProvider
from ..inference.qwen3_tts_http import Qwen3TTSHttpProvider
from ..inference.runtime import LiveRuntime, RuntimeConfig, TranscriptLine

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE / "console_static"


def _data_dir() -> Path:
    """Where the console keeps its store and aired audio.

    A frozen one-file build extracts to an ephemeral temp dir (``_MEI...``)
    that is deleted when the app quits, so resolving this from ``__file__``
    would put the database and every segment in a throwaway path — the station
    would appear to air nothing, and restart with no memory. Source runs keep
    using the project's data/ directory.

    Must agree with core.desktop._project_root(), which owns the same decision
    for the app as a whole.
    """
    if getattr(sys, "frozen", False):
        base = Path.home() / ".mindbroadcast"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return _HERE.parent.parent


_SEGMENTS_DIR = _data_dir() / "data" / "segments"

# The asyncio event-loop gotcha: capture the REAL running loop on first SSE
# connection; broadcast into the subscriber queues with call_soon_threadsafe.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_subscribers: List["asyncio.Queue"] = []
_run_lock = threading.Lock()
_run_active = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_available_tts_backend() -> str:
    """Pick the best TTS this machine can actually run, fail-closed.

    Order is quality-first, but every option is one whose *actual pre-flight*
    passes — the app never selects a backend that cannot speak. Critically,
    this must test the same condition the provider's health_check() does: a
    bare `piper` binary is not enough, because PiperTTSProvider also needs a
    voice file, and selecting it on the binary alone left the station mute
    while the UI reported a working backend.

    'silence' is the honest floor: structurally valid PCM with no voice, so the
    station still runs (and says so in the UI) rather than refusing to start.
    """
    # Best voice: the persistent Qwen3-TTS server, if it is genuinely serving.
    try:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:7861/health", timeout=2) as resp:
            import json
            if json.loads(resp.read().decode()).get("ok"):
                return "qwen3-tts-http"
    except Exception:
        pass

    # Each candidate is verified by constructing its real provider and running
    # its real health check, so the selector can never disagree with the
    # provider about whether it works.
    for backend in ("piper", "espeak", "say"):
        try:
            provider = _provider_for(backend)
            if _run_health(provider):
                return backend
        except Exception:
            continue
    return "silence"


def _provider_for(backend: str):
    """Build one TTS provider by id, without going through ModelContext."""
    from ..config import ModelContext
    return ModelContext.from_mapping({"tts": {"backend": backend}}).tts_provider()


def _run_health(provider) -> bool:
    """Run an async health check to completion WITHOUT disturbing the caller's
    event loop.

    asyncio.run() creates and then *closes* a loop, which broke unrelated
    code that later called asyncio.get_event_loop() (core/legacy_import.py),
    turning a local change into nine failures elsewhere. Use a private loop on
    a worker thread so nothing in this process is touched.
    """
    import asyncio
    import concurrent.futures

    def _check() -> bool:
        loop = asyncio.new_event_loop()
        try:
            return bool(loop.run_until_complete(provider.health_check()))
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return bool(pool.submit(_check).result(timeout=10))
    except Exception:
        return False


def _broadcast(event: Dict[str, Any]) -> None:
    """Thread-safe push to every SSE subscriber."""
    if _main_loop is None:
        return

    def _put(queue: "asyncio.Queue") -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    for queue in list(_subscribers):
        _main_loop.call_soon_threadsafe(_put, queue)


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    data = pcm
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                                    sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    path.write_bytes(header + data)


class ConsoleState:
    """Holds the runtime and the ordered list of aired segments."""

    def __init__(self) -> None:
        # Build providers from the user's mindbroadcast.yaml when present, so
        # a machine without the Qwen3-TTS model (i.e. every fresh install, the
        # release does not bundle it) can still speak via piper/espeak, or run
        # silently rather than refusing to start at all.
        self.context = self._load_context()
        self.llm = self.context.llm_provider()
        self.tts = self.context.tts_provider()
        # The backend this machine can run can IMPROVE after startup: the
        # Qwen3-TTS server takes seconds to load its model, and the console
        # starts at the same moment. Probing once meant that if the model was
        # not ready yet the station was pinned to espeak for the whole session
        # — the cloned voice (chris.wav) silently disappeared. Remember whether
        # the choice was a fallback so it can be upgraded when TTS comes up.
        self._tts_upgradable = not self._tts_is_cloned()
        self.runtime = LiveRuntime(self.llm, self.tts,
                                   config=RuntimeConfig(max_segments=0))
        # The autonomous broadcaster shares the runtime, so both the demo
        # (typed topics) and the real system (watch a source, decide, speak)
        # route aired segments through the same on_segment handler.
        self.autonomous = AutonomousBroadcaster(
            self.runtime, db_path=str(_SEGMENTS_DIR.parent / "world.db"),
            on_segment=self.on_segment)
        self.segments: List[Dict[str, Any]] = []   # ordered, playable
        _SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

    def _tts_is_cloned(self) -> bool:
        """Whether the current TTS backend can speak in the cloned voice."""
        return getattr(self.context, "tts_backend", "") in (
            "qwen3-tts-http", "qwen3-tts")

    async def refresh_tts_backend(self) -> bool:
        """Upgrade to a better TTS backend if one has become available.

        Called before a cycle airs. If the station started before the Qwen3-TTS
        model finished loading, it was pinned to a fallback voice; once the
        model is up, switch to it so the station speaks in the cloned voice
        instead of a synthetic one for the rest of the session.

        Returns True if the backend changed. Fail-closed: only ever moves to a
        backend whose real pre-flight passes, and never downgrades.
        """
        if not self._tts_upgradable:
            return False
        try:
            best = _best_available_tts_backend()
        except Exception:                        # noqa: BLE001
            return False
        if not best or best == getattr(self.context, "tts_backend", ""):
            return False
        if best not in ("qwen3-tts-http", "qwen3-tts"):
            return False   # only upgrade TO the cloned voice, never sideways

        self.context.tts_backend = best
        # Rebuild the provider and the runtime's reference to it.
        self.context._tts_provider = None
        self.tts = self.context.tts_provider()
        self.runtime.tts = self.tts
        self._tts_upgradable = False
        _broadcast({"type": "voice", "backend": best, "cloned": True})
        return True

    @staticmethod
    def _load_context():
        """Read mindbroadcast.yaml if the user (or brief-setup) wrote one.

        Search order: $MB_CONFIG, the working directory, then the app's data
        directory. Falls back to built-in defaults when none exists. The TTS
        backend is chosen from what this machine can actually run, so the app
        does not demand the heaviest model to function.
        """
        from ..config import ModelContext
        candidates = []
        env = os.environ.get("MB_CONFIG")
        if env:
            candidates.append(Path(env))
        candidates.append(Path.cwd() / "mindbroadcast.yaml")
        # The app's own data dir: where a frozen .app can actually keep it.
        candidates.append(_SEGMENTS_DIR.parent / "mindbroadcast.yaml")
        candidates.append(_data_dir() / "mindbroadcast.yaml")
        for path in candidates:
            try:
                if path.is_file():
                    ctx = ModelContext.from_path(path)
                    return ctx
            except Exception:
                continue
        # No config: pick the best TTS this machine can actually run, so a
        # machine without the Qwen3-TTS MLX stack still airs.
        ctx = ModelContext.from_mapping({})
        ctx.tts_backend = _best_available_tts_backend()
        return ctx

    def on_segment(self, line: TranscriptLine) -> None:
        """Called by the runtime as each segment airs.

        Persist the segment's audio to disk (for browser playback) and record
        its transcript entry, then push both to SSE subscribers.
        """
        segment = self.runtime.director.segments.get(line.segment_id)
        audio_url = None
        duration_ms = line.duration_ms
        if segment is not None:
            chunks = self.runtime.director.deliverable_chunks(line.segment_id)
            pcm = b"".join(c.data for c in chunks)
            if pcm:
                wav_path = _SEGMENTS_DIR / f"{line.segment_id}.wav"
                _write_wav(wav_path, pcm, 24000)
                audio_url = f"/api/segments/{line.segment_id}/audio"
        entry = {
            "segment_id": line.segment_id,
            "speaker": line.speaker,
            "text": line.text,
            "turns": line.turns,
            "duration_ms": duration_ms,
            "audio_url": audio_url,
            "at": line.at.isoformat(),
        }
        self.segments.append(entry)
        _broadcast({"type": "segment", **entry})

    async def health(self) -> Dict[str, Any]:
        return {
            "llm": await self.llm.health_check(),
            "tts": await self.tts.health_check(),
        }

    def voice_report(self) -> Dict[str, Any]:
        """Which voice is actually speaking, and whether it is the cloned one.

        The station's voice is Chris's, cloned from chris.wav. When the clip is
        missing or the TTS stack cannot do zero-shot cloning (espeak, say), the
        station still speaks — in a different voice. That difference was
        invisible, so a lost voice looked like a working station. Report it.
        """
        import os

        backend = getattr(self.context, "tts_backend", "unknown")
        cloning = backend in ("qwen3-tts-http", "qwen3-tts")
        try:
            from .voices import VoiceRegistry
            clip = VoiceRegistry().chris_clip_path()
        except Exception:                        # noqa: BLE001
            clip = None
        return {
            "backend": backend,
            "cloning": cloning,
            "clip": clip,
            "clip_present": bool(clip and os.path.isfile(clip)),
            "cloned_voice": bool(cloning and clip and os.path.isfile(clip)),
        }


state = ConsoleState()

app = FastAPI(title="MindBroadcast Console")


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    status = await state.health()
    return {"ok": all(status.values()), "services": status,
            "voice": state.voice_report()}


@app.get("/api/segments")
async def segments() -> Dict[str, Any]:
    return {"segments": state.segments}


@app.get("/api/segments/{segment_id}/audio")
async def segment_audio(segment_id: str) -> FileResponse:
    wav = _SEGMENTS_DIR / f"{segment_id}.wav"
    if not wav.exists():
        raise HTTPException(404, "segment audio not found")
    return FileResponse(wav, media_type="audio/wav", filename=wav.name)


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    """SSE: transcript + segment events as the broadcast airs."""
    global _main_loop
    if _main_loop is None:
        _main_loop = asyncio.get_running_loop()
    queue: "asyncio.Queue" = asyncio.Queue(maxsize=256)
    _subscribers.append(queue)

    async def event_source():
        try:
            # Replay what has already aired so a late joiner sees the history.
            for entry in state.segments:
                yield f"data: {json.dumps({'type': 'segment', **entry})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if queue in _subscribers:
                _subscribers.remove(queue)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.post("/api/broadcast")
async def broadcast(topics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Start a broadcast run over the given topics (one at a time)."""
    global _run_active
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "a broadcast is already running")

    health = await state.health()
    if not health.get("llm"):
        _run_lock.release()
        raise HTTPException(
            503, f"language model unavailable: {health}. Is Ollama running?")

    def run_sync() -> None:
        global _run_active
        _run_active = True
        try:
            loop = asyncio.new_event_loop()
            lines = loop.run_until_complete(
                state.runtime.run(topics, on_segment=state.on_segment))
            loop.close()
            _broadcast({"type": "done", "segments": len(lines),
                        "errors": state.runtime.errors})
        except Exception as error:               # noqa: BLE001
            _broadcast({"type": "error", "message": str(error)})
        finally:
            _run_active = False
            _run_lock.release()

    threading.Thread(target=run_sync, daemon=True).start()
    return {"accepted": True, "topics": len(topics)}


@app.post("/api/broadcast/auto")
async def broadcast_auto(body: Dict[str, Any]) -> Dict[str, Any]:
    """Run an autonomous cycle: watch a live source, decide, and speak.

    This is the real system — the broadcaster ingests a news feed, compiles it
    into claims, ranks the stories, picks the top one, and airs it. No typed
    facts. Body: {"source": "<name>", "endpoint": "<rss url>", "cycles": N}.
    """
    global _run_active
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "a broadcast is already running")

    health = await state.health()
    if not health.get("llm"):
        _run_lock.release()
        raise HTTPException(
            503, f"language model unavailable: {health}. Is Ollama running?")

    name = body.get("source", "Live Feed")
    endpoint = body.get("endpoint",
                        "https://feeds.npr.org/1001/rss.xml")
    cycles = int(body.get("cycles", 1))

    def run_sync() -> None:
        global _run_active
        _run_active = True
        aired = 0
        try:
            loop = asyncio.new_event_loop()
            source_id = loop.run_until_complete(
                state.autonomous.register_source(name, endpoint))
            for _ in range(max(1, cycles)):
                cycle = loop.run_until_complete(
                    state.autonomous.run_cycle(source_id))
                _broadcast({
                    "type": "cycle",
                    "articles": cycle.articles_ingested,
                    "claims": cycle.claims_compiled,
                    "stories": cycle.stories_ranked,
                    "story": cycle.chosen_story_title,
                    "aired": cycle.aired,
                    "silent_reason": cycle.silent_reason,
                })
                if cycle.aired:
                    aired += 1
            loop.close()
            _broadcast({"type": "done", "segments": aired,
                        "errors": state.runtime.errors})
        except Exception as error:               # noqa: BLE001
            _broadcast({"type": "error", "message": str(error)})
        finally:
            _run_active = False
            _run_lock.release()

    threading.Thread(target=run_sync, daemon=True).start()
    return {"accepted": True, "source": name, "cycles": cycles}


# ── The continuous station ───────────────────────────────────────────
# A long-lived background thread running run_forever with a real poll
# interval, so the browser can start a self-running station and stop it.

_station: Dict[str, Any] = {"running": False, "thread": None,
                            "source": None, "endpoint": None,
                            "poll_seconds": 120.0, "started_at": None,
                            "last_progress": None}


@app.post("/api/station/start")
async def station_start(body: Dict[str, Any]) -> Dict[str, Any]:
    """Start the continuous station: watch a source and air on a loop."""
    if _station["running"]:
        raise HTTPException(409, "the station is already running")

    # The LLM is required: without it there is nothing to say. TTS is NOT —
    # a machine with no voice model still ingests, ranks, and produces a
    # transcript (and airs silence). Requiring TTS here made the station
    # unusable on every fresh install, since the release bundles no model.
    health = await state.health()
    if not health.get("llm"):
        raise HTTPException(
            503, f"language model unavailable: {health}. Is Ollama running?")

    # Default to NPR: it ships full article text in the feed, which the
    # compiler turns into multiple claims per story — the basis for longform.
    # BBC's feed carries only a one-line summary per entry (article pages are
    # client-rendered), so it can only ever yield one-sentence broadcasts.
    from .settings import settings_store
    settings = settings_store.load()

    name = body.get("source", "NPR News")
    endpoint = body.get("endpoint", "https://feeds.npr.org/1001/rss.xml")
    # The saved setting is the default; an explicit request still wins, so the
    # UI can override per-run without the user having to change a setting.
    poll = float(body.get("poll_seconds") or settings.poll_seconds)
    # Optional multi-source: a list of {source, endpoint} registers several
    # feeds and runs the loop unpinned so it rotates across them (§3).
    extra = body.get("sources") or []
    # world=True registers the full curated world registry (all regions) and
    # runs unpinned so the loop rotates across every region — the station
    # represents the world, not one or two outlets. The saved setting decides
    # the default so the user's choice persists across restarts.
    use_world = bool(body.get("world", settings.use_world_feeds))

    def station_loop() -> None:
        loop = asyncio.new_event_loop()
        try:
            # Progress events: the cycle's phases, so the UI can show movement.
            state.autonomous.on_progress = _on_station_progress
            # Before the first cycle airs, give the Qwen3-TTS model a chance to
            # finish loading: the console probes once at startup, and if the
            # model was not ready it was pinned to a fallback voice for the
            # whole session — the cloned voice silently disappeared.
            try:
                loop.run_until_complete(state.refresh_tts_backend())
            except Exception:                    # noqa: BLE001
                pass
            if use_world:
                loop.run_until_complete(
                    state.autonomous.register_world_sources())
                source_label = "World"
            else:
                loop.run_until_complete(
                    state.autonomous.register_source(name, endpoint))
                for item in extra:
                    loop.run_until_complete(state.autonomous.register_source(
                        item.get("source", item.get("name", "source")),
                        item.get("endpoint", "")))
                source_label = name if not extra else f"{name} +{len(extra)}"
            # Unpinned: run_forever rotates across all enabled sources.
            _broadcast({"type": "station", "state": "on_air",
                        "source": source_label})
            loop.run_until_complete(state.autonomous.run_forever(
                source_id=None, poll_seconds=poll,
                on_cycle=_on_station_cycle, on_wait=state.refresh_tts_backend))
        except Exception as error:               # noqa: BLE001
            _broadcast({"type": "error", "message": str(error)})
        finally:
            loop.close()
            _station["running"] = False
            _broadcast({"type": "station", "state": "stopped"})

    _station.update({"running": True,
                     "source": "World" if use_world else name,
                     "endpoint": endpoint,
                     "poll_seconds": poll,
                     "started_at": datetime.now(timezone.utc).isoformat()})
    thread = threading.Thread(target=station_loop, daemon=True)
    _station["thread"] = thread
    thread.start()
    if use_world:
        from .world_sources import all_feeds
        n = len(all_feeds())
        return {"started": True, "source": "World", "sources": n,
                "poll_seconds": poll}
    return {"started": True, "source": name, "sources": 1 + len(extra),
            "poll_seconds": poll}


@app.post("/api/station/stop")
async def station_stop() -> Dict[str, Any]:
    """Stop the continuous station after the current cycle."""
    if not _station["running"]:
        return {"stopped": True, "was_running": False}
    state.autonomous.stop()
    return {"stopping": True}


@app.get("/api/station")
async def station_status() -> Dict[str, Any]:
    """Current station state, including what the cycle is doing right now.

    The UI polls this so a fresh page load (or a missed SSE event) still shows
    the real phase rather than a stale label.
    """
    last = _station.get("last_progress") or {}
    return {
        "running": _station["running"],
        "source": _station["source"],
        "poll_seconds": _station["poll_seconds"],
        "started_at": _station["started_at"],
        "cycles": len(state.autonomous.cycles),
        "aired": sum(1 for c in state.autonomous.cycles if c.aired),
        "phase": last.get("phase", ""),
        "detail": last.get("detail", ""),
        "current": last.get("current", 0),
        "total": last.get("total", 0),
    }


def _on_station_progress(event: Any) -> None:
    """Push cycle phase progress to SSE subscribers.

    A cycle takes minutes. These events are what let the UI show movement
    instead of a single "running" line that never changes.
    """
    if isinstance(event, dict):
        payload = dict(event)
    else:
        payload = {"phase": "working", "detail": str(event)}
    # Remember it so /api/station can report the phase to a late joiner.
    _station["last_progress"] = payload
    payload["type"] = "progress"
    _broadcast(payload)


def _on_station_cycle(cycle: Any) -> None:
    """Push each autonomous cycle to SSE subscribers."""
    _broadcast({
        "type": "cycle",
        "articles": cycle.articles_ingested,
        "claims": cycle.claims_compiled,
        "stories": cycle.stories_ranked,
        "story": cycle.chosen_story_title,
        "aired": cycle.aired,
        "silent_reason": cycle.silent_reason,
    })


# ── feeds: edit where the news comes from ────────────────────────────
# The station's sources are rows in the world model's sources table. These
# endpoints let the user see and change them without touching the database.

@app.get("/api/feeds")
async def list_feeds() -> Dict[str, Any]:
    """Every registered feed, with the operational state that matters.

    Includes the curated world registry, marked as such, so the user can see
    what the station watches by default and disable a region they do not want
    rather than wondering where a story came from.
    """
    from .world_sources import all_feeds

    world_urls = {s.url for s in all_feeds()}
    sources = await state.autonomous.store.list_sources()
    return {
        "feeds": [
            {
                "id": s.id,
                "name": s.name,
                "endpoint": s.endpoint,
                "enabled": s.enabled,
                "curated": s.endpoint in world_urls,
                "failure_count": s.failure_count,
                "last_success_at": (s.last_success_at.isoformat()
                                    if s.last_success_at else None),
                "last_attempt_at": (s.last_attempt_at.isoformat()
                                    if s.last_attempt_at else None),
            }
            for s in sources
        ],
        "world_available": sorted(world_urls),
    }


@app.post("/api/feeds")
async def add_feed(body: Dict[str, Any]) -> Dict[str, Any]:
    """Register a new feed, or return the existing one for that endpoint."""
    name = (body.get("name") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(400, "endpoint is required")
    if not endpoint.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "endpoint must be an http(s) URL")

    existing = await state.autonomous.store.find_source_by_endpoint(endpoint)
    if existing is not None:
        return {"id": existing.id, "name": existing.name, "existed": True}
    source_id = await state.autonomous.register_source(
        name or endpoint, endpoint)
    return {"id": source_id, "name": name or endpoint, "existed": False}


@app.patch("/api/feeds/{source_id}")
async def edit_feed(source_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a feed's name, endpoint, or enabled flag."""
    store = state.autonomous.store
    source = await store.get_source(source_id)
    if source is None:
        raise HTTPException(404, "no such feed")

    if "name" in body and body["name"] is not None:
        source.name = str(body["name"]).strip() or source.name
    if "endpoint" in body and body["endpoint"] is not None:
        endpoint = str(body["endpoint"]).strip()
        if not endpoint.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "endpoint must be an http(s) URL")
        source.endpoint = endpoint
    if "enabled" in body and body["enabled"] is not None:
        source.enabled = bool(body["enabled"])
    if "poll_interval_seconds" in body and body["poll_interval_seconds"]:
        try:
            source.poll_interval_seconds = max(
                30, int(body["poll_interval_seconds"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "poll_interval_seconds must be an integer")

    await store.update_source(source)
    return {"id": source.id, "name": source.name, "endpoint": source.endpoint,
            "enabled": source.enabled}


@app.delete("/api/feeds/{source_id}")
async def remove_feed(source_id: str) -> Dict[str, Any]:
    """Delete a feed. Refuses while articles still reference it."""
    from ..persistence import PersistenceError
    try:
        removed = await state.autonomous.store.delete_source(source_id)
    except PersistenceError as error:
        raise HTTPException(409, str(error))
    if not removed:
        raise HTTPException(404, "no such feed")
    return {"removed": True, "id": source_id}


# ── settings: the station's knobs, editable in the app ───────────────

@app.get("/api/settings")
async def get_settings() -> Dict[str, Any]:
    from .settings import MIN_POLL_SECONDS, settings_store
    return {"settings": settings_store.load().to_dict(),
            "min_poll_seconds": MIN_POLL_SECONDS}


@app.put("/api/settings")
async def put_settings(body: Dict[str, Any]) -> Dict[str, Any]:
    """Update settings. Invalid values are clamped, never rejected outright.

    A user setting a 5-second poll gets the safe floor and the response says
    what was applied, rather than an error that leaves them guessing.
    """
    from .settings import StationSettings, settings_store

    current = settings_store.load()
    merged = current.to_dict()
    merged.update(body or {})
    updated = StationSettings.from_mapping(merged)
    settings_store.save(updated)

    # Apply the live ones: a poll change should take effect on the next wait
    # rather than requiring a restart.
    if _station.get("running"):
        try:
            _station["poll_seconds"] = updated.poll_seconds
        except Exception:                        # noqa: BLE001
            pass
    return {"settings": updated.to_dict()}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = _STATIC / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>MindBroadcast</h1><p>static/index.html missing</p>")


if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
