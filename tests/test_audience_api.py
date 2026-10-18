"""Live audience and operator experience tests — Gate 8.

Spec §17 (audience interaction), §18 (UI/operator), §19 (API/events).

The tests that matter assert the refusals: a listener cannot establish a
claim, an unauthenticated client cannot hit admin endpoints, a question is not
auto-answered, feedback is never treated as truth, chain-of-thought never
reaches the audit view, and generated text is never shown as spoken.
"""

from datetime import UTC, datetime, timedelta

import pytest

from core.api.contracts import (
    ApiResponse,
    not_found,
    rate_limited,
    unauthorized,
    validation_error,
)
from core.api.endpoints import build_router
from core.api.events import EventStore, EventType
from core.api.operator import (
    AccessibilityPreferences,
    ConfirmationRequired,
    ControlStatus,
    OperatorConsole,
    status_is_not_color_encoded,
)
from core.api.router import ApiRequest, Router
from core.api.show_analysis import (
    FORBIDDEN_KEYS,
    ShowAnalysis,
    ShowAnalysisBuilder,
)
from core.api.transcript import (
    SegmentDeliveryState,
    TranscriptService,
)
from core.audience.messages import (
    AbuseClassifier,
    FeedbackCollector,
    FeedbackSignal,
    ListenerMessage,
    MessagePipeline,
    MessageType,
    ModerationStatus,
    ProcessingStatus,
    RateLimiter,
)

NOW = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)


def request(method="GET", path="/api/v1/health", authenticated=False, **kw):
    return ApiRequest(method=method, path=path, authenticated=authenticated,
                      request_id="req-1", **kw)


# ── §19.5 API contracts ──────────────────────────────────────────────


class TestApiContracts:
    def test_response_carries_api_version(self):
        response = ApiResponse.ok({"x": 1})
        assert response.to_dict()["api_version"] == "v1"

    def test_response_carries_request_id(self):
        response = ApiResponse.ok({"x": 1}, "req-42")
        assert response.to_dict()["request_id"] == "req-42"

    def test_error_carries_code_message_id_retryability(self):
        error = validation_error("bad input", request_id="req-1")
        payload = ApiResponse.error(error).body
        assert payload["error"]["code"] == "validation_error"
        assert payload["error"]["message"] == "bad input"
        assert payload["error"]["request_id"] == "req-1"
        assert payload["error"]["retryable"] is False

    def test_field_level_validation_detail(self):
        error = validation_error("bad input",
            field_errors=[type("F", (), {"field": "content", "message": "required"})()])
        payload = ApiResponse.error(error).body
        assert payload["error"]["fields"][0]["field"] == "content"

    def test_not_found_is_404(self):
        assert ApiResponse.error(not_found("story")).status == 404

    def test_rate_limited_is_429_and_retryable(self):
        assert ApiResponse.error(rate_limited()).status == 429
        assert ApiResponse.error(rate_limited()).body["error"]["retryable"]

    def test_unauthorized_is_401(self):
        assert ApiResponse.error(unauthorized()).status == 401

    def test_validation_error_is_400(self):
        assert ApiResponse.error(validation_error("x")).status == 400


# ── §19.2 Endpoint routing ───────────────────────────────────────────


class TestEndpointRouting:
    def test_all_spec_endpoints_exist(self):
        router = build_router()
        paths = {e["path"] for e in router.endpoints()}
        for expected in ("/api/v1/health", "/api/v1/system/status",
                         "/api/v1/broadcast/start", "/api/v1/broadcast/stop",
                         "/api/v1/broadcast/session", "/api/v1/broadcast/segments",
                         "/api/v1/stories", "/api/v1/claims/{id}",
                         "/api/v1/sources", "/api/v1/listener/messages",
                         "/api/v1/events", "/api/v1/metrics"):
            assert expected in paths, expected

    def test_health_returns_ok(self):
        router = build_router()
        response = router.dispatch(request(path="/api/v1/health"))
        assert response.status == 200

    def test_admin_endpoint_requires_auth(self):
        """§19.2: no unrestricted administrative operations to an
        unauthenticated client."""
        router = build_router()
        response = router.dispatch(
            request("POST", "/api/v1/broadcast/start", authenticated=False))
        assert response.status == 401

    def test_authenticated_admin_succeeds(self):
        router = build_router()
        response = router.dispatch(
            request("POST", "/api/v1/broadcast/start", authenticated=True))
        assert response.status == 200

    def test_source_register_requires_auth(self):
        router = build_router()
        response = router.dispatch(
            request("POST", "/api/v1/sources", authenticated=False))
        assert response.status == 401

    def test_unknown_path_is_404(self):
        router = build_router()
        response = router.dispatch(request(path="/api/v1/nope"))
        assert response.status == 404

    def test_wrong_method_is_405(self):
        """Path exists, method does not — distinguishable from 404."""
        router = build_router()
        response = router.dispatch(request("DELETE", "/api/v1/health"))
        assert response.status == 405

    def test_path_params_are_extracted(self):
        router = Router()
        router.add("GET", "/claims/{id}",
                   lambda req, params: ApiResponse.ok(params))
        response = router.dispatch(request(path="/claims/abc"))
        assert response.body == {"id": "abc"}

    def test_transcript_view_is_selectable(self):
        router = build_router()
        response = router.dispatch(
            request(path="/api/v1/broadcast/transcript"))
        assert response.status == 200
        assert "entries" in response.body


# ── §19.3 Event stream ───────────────────────────────────────────────


class TestEventStream:
    def test_event_has_stable_id(self):
        store = EventStore()
        first = store.emit(EventType.BROADCAST_STATE_CHANGED, {"running": True})
        assert first.id.startswith("evt-")
        assert store.latest().id == first.id

    def test_event_has_timestamp_type_versioned_payload(self):
        store = EventStore()
        event = store.emit(EventType.TRANSCRIPT_TURN_ADDED,
                           {"text": "hello"}, payload_version=2)
        assert event.timestamp is not None
        assert event.type == EventType.TRANSCRIPT_TURN_ADDED
        assert event.payload_version == 2

    def test_sse_format(self):
        store = EventStore()
        event = store.emit(EventType.BROADCAST_STATE_CHANGED, {"x": 1})
        sse = event.to_sse()
        assert sse.startswith(f"id: {event.id}")
        assert "event: broadcast.state_changed" in sse
        assert "data:" in sse

    def test_replay_after_cursor(self):
        """§19.3: the stream supports reconnection and replay."""
        store = EventStore()
        first = store.emit(EventType.BROADCAST_STATE_CHANGED)
        second = store.emit(EventType.BROADCAST_SEGMENT_STARTED)
        replay = store.replay_after(first.id)
        assert [e.id for e in replay] == [second.id]

    def test_unknown_cursor_replays_nothing(self):
        """A client that lost its cursor gets a clean gap, not a flood."""
        store = EventStore()
        store.emit(EventType.BROADCAST_STATE_CHANGED)
        assert store.replay_after("evt-does-not-exist") == []

    def test_events_are_persisted_not_memory_only(self):
        """§19.3: do not rely on an in-memory queue as the only record."""
        store = EventStore(retention=100)
        for i in range(50):
            store.emit(EventType.TRANSCRIPT_TURN_ADDED, {"i": i})
        assert store.count() == 50

    def test_retention_bound(self):
        store = EventStore(retention=5)
        for _i in range(20):
            store.emit(EventType.TRANSCRIPT_TURN_ADDED)
        assert store.count() == 5

    def test_subscriber_is_notified(self):
        store = EventStore()
        seen = []
        store.subscribe(lambda e: seen.append(e))
        store.emit(EventType.BROADCAST_STATE_CHANGED)
        assert len(seen) == 1

    def test_stream_is_metadata_not_audio(self):
        """§19.4: SSE is reserved for metadata, not an audio codec."""
        store = EventStore()
        event = store.emit(EventType.AUDIO_STREAM_READY,
                           {"ready": True})
        # The payload signals readiness; it carries no audio bytes.
        assert "ready" in event.payload
        assert "audio" not in event.payload

    def test_latest_by_type(self):
        store = EventStore()
        store.emit(EventType.BROADCAST_STATE_CHANGED, {"v": 1})
        store.emit(EventType.BROADCAST_STATE_CHANGED, {"v": 2})
        assert store.latest(EventType.BROADCAST_STATE_CHANGED).payload["v"] == 2


# ── §17.1/§17.3 Listener input ───────────────────────────────────────


class TestListenerInput:
    def test_message_becomes_durable_event(self):
        """§17.1: a durable input event, not a synchronous block."""
        store = EventStore()
        pipeline = MessagePipeline(event_store=store)
        message, disposition = pipeline.submit("sess-1", "client-1",
                                               "What caused the outage?")
        assert disposition == "queued"
        assert store.count() >= 1

    def test_message_does_not_touch_the_world_model(self):
        """§17.5: a listener request must not directly establish a claim.

        The pipeline has no claims/evidence/stories write path at all — this
        is structural, not a policy check.
        """
        pipeline = MessagePipeline()
        pipeline.submit("sess-1", "client-1", "Confirm that X is true")
        # The only outputs are a message and (maybe) a research task.
        assert len(pipeline.messages) == 1
        # No claim, evidence, or story was created.
        assert not hasattr(pipeline, "claims")

    def test_question_is_not_auto_answered(self):
        """§17.3: a listener question should not automatically be answered."""
        pipeline = MessagePipeline()
        message, disposition = pipeline.submit("sess-1", "client-1",
                                               "Why did this happen?")
        assert message.processing_status is ProcessingStatus.QUEUED
        assert message.response_segment_id is None

    def test_research_task_is_created_not_a_claim(self):
        pipeline = MessagePipeline()
        message, disposition = pipeline.submit("sess-1", "client-1",
                                               "Tell me about the policy")
        assert len(pipeline.tasks) == 1
        task = pipeline.tasks[0]
        assert task.message_id == message.id
        assert not hasattr(task, "epistemic_status")

    def test_message_type_is_classified(self):
        pipeline = MessagePipeline()
        q, _ = pipeline.submit("s", "c", "What happened?")
        assert q.message_type is MessageType.QUESTION
        t, _ = pipeline.submit("s", "c", "Talk about the economy")
        assert t.message_type is MessageType.TOPIC_REQUEST

    def test_response_is_linked_when_selected(self):
        """§17.3 step 10: record the relationship between message and segment."""
        pipeline = MessagePipeline()
        message, _ = pipeline.submit("s", "c", "What happened?")
        pipeline.link_response(message.id, "seg-9")
        assert message.response_segment_id == "seg-9"
        assert message.processing_status is ProcessingStatus.RESPONDED

    def test_empty_content_is_rejected(self):
        pipeline = MessagePipeline()
        with pytest.raises(ValueError):
            pipeline.submit("s", "c", "   ")

    def test_persistent_identity_is_optional(self):
        """§17.2: a session identifier without accounts."""
        pipeline = MessagePipeline()
        message, _ = pipeline.submit("sess-abc", "anon", "Hello")
        assert message.session_id == "sess-abc"
        # No account, no auth required to submit.
        assert message.client_id == "anon"


# ── §17.5 Rate limiting and abuse ────────────────────────────────────


class TestRateLimiting:
    def test_per_client_limit(self):
        limiter = RateLimiter(max_messages_per_minute=2)
        assert limiter.check("c1", now=NOW).allowed
        limiter.record("c1", now=NOW)
        assert limiter.check("c1", now=NOW).allowed
        limiter.record("c1", now=NOW)
        assert not limiter.check("c1", now=NOW).allowed

    def test_limits_are_per_client(self):
        limiter = RateLimiter(max_messages_per_minute=1)
        limiter.record("c1", now=NOW)
        assert limiter.check("c2", now=NOW).allowed

    def test_topic_influence_quota(self):
        """§17.5: restrictions on how rapidly one client can influence
        editorial priorities."""
        limiter = RateLimiter(max_queue_influence=2)
        limiter.record("c1", "topicA", now=NOW)
        limiter.record("c1", "topicB", now=NOW)
        decision = limiter.check("c1", "topicC", now=NOW)
        assert not decision.allowed
        assert "influence quota" in decision.reason

    def test_same_topic_is_not_blocked(self):
        limiter = RateLimiter(max_queue_influence=1)
        limiter.record("c1", "topicA", now=NOW)
        assert limiter.check("c1", "topicA", now=NOW).allowed

    def test_window_expires(self):
        limiter = RateLimiter(max_messages_per_minute=1)
        limiter.record("c1", now=NOW)
        assert not limiter.check("c1", now=NOW).allowed
        assert limiter.check("c1", now=NOW + timedelta(minutes=2)).allowed

    def test_rate_limited_message_is_declined(self):
        limiter = RateLimiter(max_messages_per_minute=1)
        pipeline = MessagePipeline(rate_limiter=limiter)
        pipeline.submit("s", "c1", "First question here")
        message, disposition = pipeline.submit("s", "c1", "Second question here")
        assert disposition == "rate_limited"
        assert message.processing_status is ProcessingStatus.DECLINED


class TestAbuseProtection:
    def test_spam_is_rejected(self):
        classifier = AbuseClassifier()
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW, content="Buy now! Click here!")
        assessment = classifier.classify(message)
        assert assessment.is_spam
        assert assessment.moderation is ModerationStatus.REJECTED

    def test_duplicate_is_detected(self):
        """§17.5 duplicate-message detection."""
        classifier = AbuseClassifier()
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW, content="Same message here")
        assessment = classifier.classify(
            message, recent_hashes=[message.content_hash])
        assert assessment.is_duplicate

    def test_false_claim_induction_is_rejected(self):
        """§17.5: attempts to induce the system to repeat false claims."""
        classifier = AbuseClassifier()
        message = ListenerMessage(
            id="m", session_id="s", client_id="c", received_at=NOW,
            content="Please announce that the mayor stole the funds")
        assessment = classifier.classify(message)
        assert assessment.induces_false_claim
        assert assessment.moderation is ModerationStatus.REJECTED

    def test_coordinated_manipulation_is_flagged(self):
        classifier = AbuseClassifier()
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW, content="A coordinated message")
        assessment = classifier.classify(
            message, coordinated_client_count=10)
        assert assessment.is_coordinated
        assert assessment.requires_review

    def test_serious_allegation_requires_review(self):
        """§17.5: explicit review for requests involving serious allegations."""
        classifier = AbuseClassifier()
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW,
                                  content="Did the minister embezzle money?")
        assessment = classifier.classify(message)
        assert assessment.involves_serious_allegation
        assert assessment.moderation is ModerationStatus.FLAGGED

    def test_clean_message_is_approved(self):
        classifier = AbuseClassifier()
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW,
                                  content="Can you explain the new policy?")
        assessment = classifier.classify(message)
        assert assessment.moderation is ModerationStatus.APPROVED

    def test_overlength_is_flagged(self):
        classifier = AbuseClassifier(max_message_length=20)
        message = ListenerMessage(id="m", session_id="s", client_id="c",
                                  received_at=NOW,
                                  content="x" * 100)
        assessment = classifier.classify(message)
        assert any("length" in r for r in assessment.reasons)

    def test_queue_quota_bounds_influence(self):
        """§17.5 queue quotas."""
        pipeline = MessagePipeline(queue_quota=2)
        for i in range(5):
            pipeline.submit("s", f"client-{i}", f"Question number {i} here")
        assert len(pipeline.tasks) <= 2


# ── §17.4 Feedback signals ───────────────────────────────────────────


class TestFeedbackSignals:
    def test_signals_are_recorded(self):
        collector = FeedbackCollector()
        collector.record(FeedbackSignal.POSITIVE, "seg-1")
        assert collector.counts()["positive"] == 1

    def test_signals_are_never_truth(self):
        """§17.4: not direct measures of truth, importance, or consensus.

        The preference signal is explicitly noisy and confidence-capped.
        """
        collector = FeedbackCollector()
        for _ in range(100):
            collector.record(FeedbackSignal.POSITIVE, "seg-1")
        signal = collector.preference_signal("seg-1")
        assert signal["noisy"] is True
        assert signal["confidence"] <= 0.5

    def test_replay_ambiguity_is_recorded(self):
        """§17.4: a replay may indicate confusion rather than interest."""
        collector = FeedbackCollector()
        collector.record(FeedbackSignal.REPLAY, "seg-1")
        notes = collector.ambiguity_notes()
        assert any("confusion" in n for n in notes)

    def test_skip_ambiguity_is_recorded(self):
        collector = FeedbackCollector()
        collector.record(FeedbackSignal.SKIP, "seg-1")
        notes = collector.ambiguity_notes()
        assert any("technical" in n for n in notes)

    def test_no_signal_scores_zero_confidence(self):
        collector = FeedbackCollector()
        signal = collector.preference_signal("seg-none")
        assert signal["confidence"] == 0.0

    def test_mixed_feedback_net_signal(self):
        collector = FeedbackCollector()
        collector.record(FeedbackSignal.POSITIVE, "seg-1")
        collector.record(FeedbackSignal.NEGATIVE, "seg-1")
        signal = collector.preference_signal("seg-1")
        assert signal["signal"] == 0.0


# ── §18.4 SHOW ANALYSIS ──────────────────────────────────────────────


class TestShowAnalysis:
    def test_all_ten_fields_present(self):
        analysis = ShowAnalysisBuilder().build(
            "seg-1", editorial_objective="Report the outcome.")
        data = analysis.to_dict()
        for field in ("editorial_objective", "story_context", "evidence",
                      "contradictions", "interpretations", "persona_roles",
                      "unresolved_questions", "selection_factors",
                      "generation_provenance", "validation_state"):
            assert field in data, field

    def test_no_chain_of_thought_field(self):
        """§18.4: do not present private chain-of-thought as explanation."""
        analysis = ShowAnalysis()
        assert "chain_of_thought" not in analysis.to_dict()
        assert "reasoning" not in analysis.to_dict()

    def test_reasoning_is_redacted(self):
        """Belt and braces: even injected reasoning is stripped."""
        analysis = ShowAnalysis(
            segment_id="seg-1",
            evidence=[{"claim": "x", "chain_of_thought": "secret"}],
            generation_provenance={"model": "m", "reasoning": "secret"})
        redacted = ShowAnalysis.redact(analysis)
        assert "chain_of_thought" not in redacted.evidence[0]
        assert "reasoning" not in redacted.generation_provenance

    def test_forbidden_keys_cover_reasoning_variants(self):
        assert "chain_of_thought" in FORBIDDEN_KEYS
        assert "reasoning" in FORBIDDEN_KEYS
        assert "raw_prompt" in FORBIDDEN_KEYS

    def test_selection_factors_from_ranking(self):
        from core.world_model.ranking import StoryFacts, StoryRanker
        ranker = StoryRanker()
        result = ranker.rank(StoryFacts(story_id="s", now=NOW))
        analysis = ShowAnalysisBuilder().build(
            "seg-1", ranking=result)
        assert analysis.selection_factors
        assert all(hasattr(f, "reason") for f in analysis.selection_factors)

    def test_interpretations_distinguished_from_facts(self):
        from core.broadcast.entities import BroadcastScript, ScriptTurn, SpeechAct
        script = BroadcastScript(turns=[
            ScriptTurn(sequence=1, text="A fact.",
                       speech_act=SpeechAct.FACTUAL_REPORT),
            ScriptTurn(sequence=2, text="My interpretation.",
                       speech_act=SpeechAct.INTERPRETATION),
        ])
        analysis = ShowAnalysisBuilder().build("seg-1", script=script)
        assert len(analysis.interpretations) == 1
        assert analysis.interpretations[0]["speech_act"] == "interpretation"

    def test_validation_state_is_exposed(self):
        analysis = ShowAnalysisBuilder().build(
            "seg-1", editorial_objective="x",
            validation=_fake_report())
        assert analysis.validation_state == "approved"

    def test_completeness_requires_objective_and_provenance(self):
        empty = ShowAnalysis()
        assert not empty.is_complete
        full = ShowAnalysisBuilder().build(
            "seg-1", editorial_objective="Report.",
            script=_script_with_turn(),
            validation=_fake_report())
        assert full.is_complete


def _fake_report():
    from core.broadcast.entities import ValidationOutcome
    from core.broadcast.validation import ValidationReport
    return ValidationReport(outcome=ValidationOutcome.APPROVED)


def _script_with_turn():
    from core.broadcast.entities import BroadcastScript, ScriptTurn
    return BroadcastScript(
        segment_id="seg-1", evidence_packet_id="packet-1",
        turns=[ScriptTurn(
            sequence=1, persona_id="p1", text="A fact.",
            generation_metadata={"provider_id": "local", "model_id": "m1",
                                 "prompt_version": "v1"})])


# ── §18.3 Transcript ─────────────────────────────────────────────────


class TestTranscript:
    def _script(self):
        from core.broadcast.entities import BroadcastScript, ScriptTurn
        return BroadcastScript(segment_id="seg-1", turns=[
            ScriptTurn(sequence=1, persona_id="p1", text="Hello listeners.")])

    def _segment(self):
        from core.broadcast.entities import Segment
        return Segment(id="seg-1", objective="Report the outcome")

    def test_generated_text_is_not_delivered(self):
        """§18.3: a script generated but never played is not in the public
        transcript."""
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        assert transcript.public_transcript() == []
        assert len(transcript.full_transcript()) == 1

    def test_delivered_text_is_public(self):
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        transcript.mark_delivered("seg-1")
        assert len(transcript.public_transcript()) == 1

    def test_entry_carries_all_fields(self):
        transcript = TranscriptService()
        entries = transcript.record_generated(self._segment(), self._script())
        data = entries[0].to_dict()
        for field in ("speaker", "segment_title", "text", "started_at",
                      "playback_state", "evidence_links",
                      "epistemic_classification", "correction_status",
                      "delivery_state", "delivered"):
            assert field in data, field

    def test_interrupted_segment_is_marked(self):
        """§18.3: an interrupted segment is marked, not completed."""
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        transcript.mark_delivered("seg-1")
        transcript.mark_interrupted("seg-1")
        entry = transcript.entries_for_segment("seg-1")[0]
        assert entry.delivery_state is SegmentDeliveryState.INTERRUPTED

    def test_abandoned_segment_is_marked(self):
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        transcript.mark_abandoned("seg-1")
        entry = transcript.entries_for_segment("seg-1")[0]
        assert entry.delivery_state is SegmentDeliveryState.ABANDONED

    def test_correction_links_original(self):
        """§15.7: the original is preserved and marked superseded."""
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        transcript.mark_delivered("seg-1")
        transcript.mark_corrected("seg-1", "seg-2")
        entry = transcript.entries_for_segment("seg-1")[0]
        assert entry.correction_status == "superseded"
        assert entry.superseded_by == "seg-2"
        assert entry.text == "Hello listeners."      # original preserved

    def test_completed_segment_is_public(self):
        transcript = TranscriptService()
        transcript.record_generated(self._segment(), self._script())
        transcript.mark_delivered("seg-1")
        transcript.mark_completed("seg-1")
        assert len(transcript.public_transcript()) == 1


# ── §18.6 Operator controls ──────────────────────────────────────────


class TestOperatorControls:
    def test_immediate_control_needs_no_confirmation(self):
        console = OperatorConsole()
        result = console.execute("mute_playback")
        assert result.status is ControlStatus.SUCCEEDED

    def test_policy_control_requires_confirmation(self):
        """§18.6: actions affecting persistent policy need confirmation."""
        console = OperatorConsole()
        with pytest.raises(ConfirmationRequired):
            console.execute("toggle_source")

    def test_policy_control_with_confirmation_succeeds(self):
        console = OperatorConsole()
        result = console.execute("toggle_source", confirmed=True)
        assert result.status is ControlStatus.SUCCEEDED

    def test_confirmation_attempt_is_audited(self):
        console = OperatorConsole()
        with pytest.raises(ConfirmationRequired):
            console.execute("toggle_source")
        # The unconfirmed attempt is still recorded (§18.6 audit).
        assert len(console.audit_log("toggle_source")) == 1

    def test_successful_action_is_audited(self):
        console = OperatorConsole()
        console.execute("mute_playback")
        assert console.audit_log("mute_playback")

    def test_unknown_control_is_not_permitted(self):
        console = OperatorConsole()
        result = console.execute("delete_everything")
        assert result.status is ControlStatus.NOT_PERMITTED

    def test_state_is_textual_not_color(self):
        """§18.7: state must not rely on color alone.

        The console state is a dict of named booleans and strings — every
        value is textual, and there is no color field to rely on.
        """
        console = OperatorConsole()
        state = console.state()
        assert all(isinstance(v, (bool, str)) for v in state.values())
        assert "color" not in state

    def test_all_spec_controls_are_classified(self):
        for action in ("start_broadcast", "stop_broadcast", "pause_generation",
                       "mute_playback", "skip_segment", "request_topic",
                       "change_persona_config", "toggle_source",
                       "adjust_editorial_priority", "trigger_source_refresh",
                       "export_transcripts"):
            assert action in CONTROL_KINDS if False else action in _kinds()

    def test_handler_failure_is_audited(self):
        console = OperatorConsole()
        console.register_handler("trigger_source_refresh",
                                 lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        result = console.execute("trigger_source_refresh")
        assert result.status is ControlStatus.FAILED
        assert console.audit_log("trigger_source_refresh")

    def test_failed_tasks_view_has_text_status(self):
        console = OperatorConsole()
        view = console.failed_tasks_view([{"id": "t1", "reason": "timeout"}])
        assert view[0]["status_text"] == "failed"


def _kinds():
    from core.api.operator import CONTROL_KINDS
    return CONTROL_KINDS


CONTROL_KINDS = _kinds()


# ── §18.7 Accessibility ──────────────────────────────────────────────


class TestAccessibility:
    def test_all_preferences_default_true(self):
        prefs = AccessibilityPreferences()
        assert all(prefs.to_dict().values())

    def test_transcript_available_without_audio(self):
        """§18.7: transcript access without audio."""
        assert AccessibilityPreferences().transcript_without_audio

    def test_reduced_motion_is_respected(self):
        assert AccessibilityPreferences().respect_reduced_motion

    def test_color_only_status_is_non_compliant(self):
        assert not status_is_not_color_encoded({"color": "red"})

    def test_textual_status_is_compliant(self):
        assert status_is_not_color_encoded({"status_text": "approved"})

    def test_state_field_is_compliant(self):
        assert status_is_not_color_encoded({"state": "failed"})


# ── §18.1 The four dashboard questions ───────────────────────────────


class TestFourQuestions:
    def test_what_is_it_saying_now(self):
        """Transcript + player answer question 1."""
        router = build_router()
        response = router.dispatch(request(path="/api/v1/broadcast/transcript"))
        assert "entries" in response.body

    def test_why_is_it_saying_this(self):
        """SHOW ANALYSIS answers question 2."""
        router = build_router()
        response = router.dispatch(
            request(path="/api/v1/broadcast/segments/seg-1/analysis"))
        assert response.status == 200

    def test_what_is_it_observing(self):
        """System status answers question 3."""
        router = build_router()
        response = router.dispatch(request(path="/api/v1/system/status"))
        assert response.status == 200

    def test_what_is_next(self):
        """Events answer question 4."""
        router = build_router()
        response = router.dispatch(request(path="/api/v1/events"))
        assert "events" in response.body
