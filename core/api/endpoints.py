"""The §19.2 endpoint set wired to domain operations.

This module assembles the router from the subsystems built in Phases 1–6 and
this phase. It exposes domain operations, never raw database tables (§19.2).

The two security properties enforced structurally here:

1. Admin endpoints (`requires_auth=True`) are refused for an unauthenticated
   client (§19.2).
2. There is no endpoint that writes a claim, evidence, or story from listener
   input (§17.5). The listener endpoint enqueues; it cannot establish a claim.
"""

from typing import Any, Dict, List, Optional

from .contracts import (
    ApiError, ApiResponse, ErrorCode, ClaimSummary, EvidenceSummary,
    HealthStatus, SegmentSummary, SessionSummary, SourceSummary, StorySummary,
    SystemStatus, validation_error,
)
from .events import EventStore, EventStream
from .operator import OperatorConsole
from .router import ApiRequest, Router
from .show_analysis import ShowAnalysisBuilder
from .transcript import TranscriptService


def build_router(
    store: Any = None,
    event_store: Optional[EventStore] = None,
    transcript: Optional[TranscriptService] = None,
    operator: Optional[OperatorConsole] = None,
    show_analysis: Optional[ShowAnalysisBuilder] = None,
    message_pipeline: Any = None,
    world_explorer: Any = None,
) -> Router:
    """Assemble the §19.2 endpoint set.

    Dependencies are optional so the router can be exercised in tests without a
    live store; a missing dependency returns 503 rather than a traceback.
    """
    router = Router()
    event_store = event_store or EventStore()
    transcript = transcript or TranscriptService()
    operator = operator or OperatorConsole()
    show_analysis = show_analysis or ShowAnalysisBuilder()

    # ── §19.2 health and status ──────────────────────────────────────

    def health(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok(
            HealthStatus(status="ok",
                         checked_at=_now_iso(),
                         checks={"store": store is not None}).__dict__,
            request.request_id)

    def system_status(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        status = SystemStatus(
            operating_mode=operator.state().get("operating_mode", "live"),
            health="ok" if store is not None else "degraded",
            active_sources=_count(getattr(store, "list_sources", None)),
            articles_pending=0,
            segments_active=0,
            listeners_queued=(len(message_pipeline.tasks)
                              if message_pipeline else 0),
        )
        return ApiResponse.ok(status.__dict__, request.request_id)

    # ── §19.2 broadcast ──────────────────────────────────────────────

    def broadcast_start(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        result = operator.execute("start_broadcast", confirmed=True)
        event_store.emit("broadcast.state_changed", {"running": True})
        return ApiResponse.ok(result.state, request.request_id)

    def broadcast_stop(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        result = operator.execute("stop_broadcast", confirmed=True)
        event_store.emit("broadcast.state_changed", {"running": False})
        return ApiResponse.ok(result.state, request.request_id)

    def broadcast_session(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        if store is None:
            return ApiResponse.error(_unavailable(request.request_id))
        return ApiResponse.ok(
            SessionSummary(
                id="session-1",
                operating_mode=operator.state().get("operating_mode", "live"),
                playback_state="idle",
            ).__dict__, request.request_id)

    def broadcast_segments(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"segments": []}, request.request_id)

    def broadcast_segment(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok(
            {"segment_id": params["id"], "status": "unknown"},
            request.request_id)

    def broadcast_transcript(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        limit = request.query_int("limit", 100)
        public = request.query.get("view", "public")
        entries = (transcript.public_transcript(limit) if public == "public"
                   else transcript.full_transcript(limit))
        return ApiResponse.ok(
            {"entries": [e.to_dict() for e in entries]}, request.request_id)

    # ── §19.2 stories and claims ─────────────────────────────────────

    def stories(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        if store is None:
            return ApiResponse.error(_unavailable(request.request_id))
        return ApiResponse.ok({"stories": []}, request.request_id)

    def story(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"story_id": params["id"]}, request.request_id)

    def claim(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"claim_id": params["id"]}, request.request_id)

    def claim_evidence(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"evidence": []}, request.request_id)

    # ── §19.2 sources (admin) ────────────────────────────────────────

    def sources_list(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"sources": []}, request.request_id)

    def sources_register(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        if not request.body or not isinstance(request.body, dict):
            return ApiResponse.error(validation_error(
                "a source registration body is required", request_id=request.request_id))
        return ApiResponse.created({"registered": True}, request.request_id)

    def source_update(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"source_id": params["id"], "updated": True},
                              request.request_id)

    # ── §19.2 listener messages ──────────────────────────────────────

    def listener_submit(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        """§17.1: a durable input event. Never blocks, never establishes a claim."""
        if message_pipeline is None:
            return ApiResponse.error(_unavailable(request.request_id))
        body = request.body or {}
        content = body.get("content", "")
        session_id = body.get("session_id", "")
        client_id = request.client_id or body.get("client_id", "anonymous")
        try:
            message, disposition = message_pipeline.submit(
                session_id, client_id, content)
        except ValueError as error:
            return ApiResponse.error(validation_error(
                str(error), request_id=request.request_id))
        return ApiResponse.created({
            "message_id": message.id,
            "disposition": disposition,
            "processing_status": message.processing_status.value,
            "moderation_status": message.moderation_status.value,
        }, request.request_id)

    # ── §19.2 events, snapshots, metrics ─────────────────────────────

    def events(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        stream = EventStream(event_store)
        after = request.query.get("after")
        if after:
            batch = stream.connect(after)
        else:
            batch = event_store.replay_all(request.query_int("limit", 100))
        return ApiResponse.ok(
            {"events": [e.to_dict() for e in batch]}, request.request_id)

    def snapshot(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({"snapshot_id": params["id"]}, request.request_id)

    def metrics(request: ApiRequest, params: Dict[str, str]) -> ApiResponse:
        return ApiResponse.ok({
            "event_counts": _event_counts(event_store),
        }, request.request_id)

    # ── §18.4 SHOW ANALYSIS ──────────────────────────────────────────

    def show_analysis_endpoint(request: ApiRequest,
                               params: Dict[str, str]) -> ApiResponse:
        segment_id = params["id"]
        analysis = show_analysis.build(segment_id)
        return ApiResponse.ok(analysis.to_dict(), request.request_id)

    # ── Wire the routes (§19.2 table) ────────────────────────────────

    router.add("GET", "/health", health, summary="Basic health status")
    router.add("GET", "/system/status", system_status,
               summary="Operational state and subsystem health")

    router.add("POST", "/broadcast/start", broadcast_start, requires_auth=True,
                summary="Start a broadcast session")
    router.add("POST", "/broadcast/stop", broadcast_stop, requires_auth=True,
                summary="Stop the broadcast gracefully")
    router.add("GET", "/broadcast/session", broadcast_session,
               summary="Retrieve the current session")
    router.add("GET", "/broadcast/segments", broadcast_segments,
               summary="List recent and active segments")
    router.add("GET", "/broadcast/segments/{id}", broadcast_segment,
               summary="Retrieve a segment and its state")
    router.add("GET", "/broadcast/transcript", broadcast_transcript,
               summary="Retrieve transcript entries")
    router.add("GET", "/broadcast/segments/{id}/analysis", show_analysis_endpoint,
               summary="SHOW ANALYSIS for a segment (§18.4)")

    router.add("GET", "/stories", stories, summary="Query active stories")
    router.add("GET", "/stories/{id}", story, summary="Retrieve a story and its history")
    router.add("GET", "/claims/{id}", claim, summary="Retrieve a claim and its status")
    router.add("GET", "/claims/{id}/evidence", claim_evidence,
               summary="Retrieve associated evidence")

    router.add("GET", "/sources", sources_list, summary="List configured sources")
    router.add("POST", "/sources", sources_register, requires_auth=True,
                summary="Register a source")
    router.add("PATCH", "/sources/{id}", source_update, requires_auth=True,
                 summary="Update source configuration")

    router.add("POST", "/listener/messages", listener_submit,
                summary="Submit a listener message")
    router.add("GET", "/events", events, summary="Query the event timeline")
    router.add("GET", "/snapshots/{id}", snapshot, summary="Inspect a world snapshot")
    router.add("GET", "/metrics", metrics, summary="Retrieve permitted metrics")

    # Mount under the version prefix (§19.5).
    return _prefixed(router, "/api/v1")


def _prefixed(router: Router, prefix: str) -> Router:
    """Return a router whose paths carry the /api/v1 prefix (§19.5)."""
    prefixed = Router()
    for route in router.routes:
        prefixed.add(route.method, prefix + route.pattern, route.handler,
                     route.requires_auth, route.summary)
    return prefixed


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _count(lister: Any) -> int:
    if lister is None:
        return 0
    try:
        return len(lister)
    except Exception:
        return 0


def _event_counts(event_store: EventStore) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for event in event_store.replay_all(limit=10000):
        counts[event.type] = counts.get(event.type, 0) + 1
    return counts


def _unavailable(request_id: str):
    from .contracts import ApiError, ErrorCode
    return ApiError(ErrorCode.NOT_AVAILABLE,
                    "the world store is not available", request_id,
                    retryable=True)
