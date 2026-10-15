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
_SEGMENTS_DIR = _HERE.parent.parent / "data" / "segments"

# The asyncio event-loop gotcha: capture the REAL running loop on first SSE
# connection; broadcast into the subscriber queues with call_soon_threadsafe.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_subscribers: List["asyncio.Queue"] = []
_run_lock = threading.Lock()
_run_active = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.llm = OllamaProvider(model=os.environ.get(
            "MBS_MODEL", "qwen3:30b-a3b"))
        self.tts = Qwen3TTSHttpProvider()
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


state = ConsoleState()

app = FastAPI(title="MindBroadcast Console")


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    status = await state.health()
    return {"ok": all(status.values()), "services": status}


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
    if not all(health.values()):
        _run_lock.release()
        raise HTTPException(503, f"providers unavailable: {health}")

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
    if not all(health.values()):
        _run_lock.release()
        raise HTTPException(503, f"providers unavailable: {health}")

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
                            "poll_seconds": 120.0, "started_at": None}


@app.post("/api/station/start")
async def station_start(body: Dict[str, Any]) -> Dict[str, Any]:
    """Start the continuous station: watch a source and air on a loop."""
    if _station["running"]:
        raise HTTPException(409, "the station is already running")

    health = await state.health()
    if not all(health.values()):
        raise HTTPException(503, f"providers unavailable: {health}")

    # Default to NPR: it ships full article text in the feed, which the
    # compiler turns into multiple claims per story — the basis for longform.
    # BBC's feed carries only a one-line summary per entry (article pages are
    # client-rendered), so it can only ever yield one-sentence broadcasts.
    name = body.get("source", "NPR News")
    endpoint = body.get("endpoint", "https://feeds.npr.org/1001/rss.xml")
    poll = float(body.get("poll_seconds", 120))
    # Optional multi-source: a list of {source, endpoint} registers several
    # feeds and runs the loop unpinned so it rotates across them (§3).
    extra = body.get("sources") or []

    def station_loop() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                state.autonomous.register_source(name, endpoint))
            for item in extra:
                loop.run_until_complete(state.autonomous.register_source(
                    item.get("source", item.get("name", "source")),
                    item.get("endpoint", "")))
            # Unpinned: run_forever rotates across all enabled sources.
            source_label = name if not extra else f"{name} +{len(extra)}"
            _broadcast({"type": "station", "state": "on_air",
                        "source": source_label})
            loop.run_until_complete(state.autonomous.run_forever(
                source_id=None, poll_seconds=poll,
                on_cycle=_on_station_cycle))
        except Exception as error:               # noqa: BLE001
            _broadcast({"type": "error", "message": str(error)})
        finally:
            loop.close()
            _station["running"] = False
            _broadcast({"type": "station", "state": "stopped"})

    _station.update({"running": True, "source": name, "endpoint": endpoint,
                     "poll_seconds": poll,
                     "started_at": datetime.now(timezone.utc).isoformat()})
    thread = threading.Thread(target=station_loop, daemon=True)
    _station["thread"] = thread
    thread.start()
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
    return {
        "running": _station["running"],
        "source": _station["source"],
        "poll_seconds": _station["poll_seconds"],
        "started_at": _station["started_at"],
        "cycles": len(state.autonomous.cycles),
        "aired": sum(1 for c in state.autonomous.cycles if c.aired),
    }


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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = _STATIC / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>MindBroadcast</h1><p>static/index.html missing</p>")


if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
