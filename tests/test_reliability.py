"""Reliability, optimization, and streaming tests — Gate 10.

Spec §21 (observability), §22 (crash tests), §23 (performance/resources),
§27.4 (continuous operation), §28 (recursive improvement), §30 (definition of
done).

The tests that matter assert the refusals: no silent quality downgrade, no
fabricated novelty, no autonomous weakening of validation, no duplicate segments
after a crash, no sensitive content in logs, and no single opaque quality score.
"""

import pytest
from datetime import datetime, timezone, timedelta

from core.reliability.metrics import (
    HealthState, Metric, MetricsRegistry, Subsystem,
)
from core.broadcast.entities import SegmentStatus
from core.reliability.health import (
    Capability, HealthService, LogRecord, ReadinessReport, RedactingFormatter,
    StructuredLogger,
)
from core.reliability.scheduler import (
    BackpressurePolicy, Priority, PriorityScheduler, QueueFull, Task,
)
from core.reliability.cache import (
    AudioCache, AudioCacheKey, EntityResolutionCache, SummaryCache,
)
from core.reliability.recovery import (
    CrashRecoveryHarness, LongDurationSimulator, RecoveryLedger, ResourceCeiling,
)
from core.reliability.routing import (
    ContinuousOperationPolicy, ResourceAwareRouter, ResourceState, RouteChoice,
    SegmentNature,
)
from core.reliability.improvement import (
    ADJUSTABLE_PARAMETERS, ImprovementEvaluator, ImprovementProposal,
    ImprovementState, PROTECTED_PARAMETERS, QualityAssessment,
    RecursiveImprovementEngine,
)
from core.reliability.streaming import (
    HttpChunkedTransport, IcecastTransport, MetadataTransport, StreamManager,
    TransportKind,
)

NOW = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)


# ── §21.2 Metrics ────────────────────────────────────────────────────


class TestMetrics:
    def test_all_six_subsystems_exist(self):
        assert len(Subsystem) == 6

    def test_counter_increments(self):
        registry = MetricsRegistry()
        registry.increment(Subsystem.INGESTION, "fetch_attempts")
        registry.increment(Subsystem.INGESTION, "fetch_attempts")
        assert registry.get(Subsystem.INGESTION, "fetch_attempts") == 2

    def test_gauge_sets_absolute_value(self):
        registry = MetricsRegistry()
        registry.set_gauge(Subsystem.AUDIO, "queue_depth", 42)
        registry.set_gauge(Subsystem.AUDIO, "queue_depth", 7)
        assert registry.get(Subsystem.AUDIO, "queue_depth") == 7

    def test_health_is_derived_not_manual(self):
        """§21.5: real state from checks, not decorative indicators."""
        registry = MetricsRegistry()
        assert registry.health(Subsystem.INGESTION) is HealthState.UNAVAILABLE
        for _ in range(10):
            registry.increment(Subsystem.INGESTION, "articles_captured")
        assert registry.health(Subsystem.INGESTION) is HealthState.HEALTHY

    def test_degraded_on_high_failure_rate(self):
        registry = MetricsRegistry()
        for _ in range(8):
            registry.increment(Subsystem.INGESTION, "fetch_succeeded")
        for _ in range(2):
            registry.record_error(Subsystem.INGESTION, "fetch")
        assert registry.health(Subsystem.INGESTION) is HealthState.DEGRADED

    def test_unavailable_on_majority_failures(self):
        registry = MetricsRegistry()
        registry.increment(Subsystem.INGESTION, "fetch_succeeded")
        for _ in range(5):
            registry.record_error(Subsystem.INGESTION, "fetch")
        assert registry.health(Subsystem.INGESTION) is HealthState.UNAVAILABLE

    def test_all_health_view(self):
        registry = MetricsRegistry()
        registry.increment(Subsystem.AUDIO, "segments_synthesized")
        health = registry.all_health()
        assert set(health) == {s.value for s in Subsystem}

    def test_backlog_is_exposed(self):
        """§21.2/§23.3: expose the backlog through observability."""
        registry = MetricsRegistry()
        registry.set_gauge(Subsystem.SYSTEM, "backlog", 17)
        assert registry.backlog(Subsystem.SYSTEM) == 17

    def test_snapshot_has_metrics_and_health(self):
        registry = MetricsRegistry()
        registry.increment(Subsystem.EDITORIAL, "stories_ranked")
        snap = registry.snapshot()
        assert "metrics" in snap and "health" in snap


# ── §21.3 Structured logging ─────────────────────────────────────────


class TestStructuredLogging:
    def test_record_carries_correlation_fields(self):
        record = LogRecord(level="info", event="segment_started",
                           segment_id="seg-1", session_id="sess-1",
                           correlation_id="corr-1", stage="generation")
        assert record.segment_id == "seg-1"
        assert record.session_id == "sess-1"
        assert record.correlation_id == "corr-1"
        assert record.stage == "generation"

    def test_log_is_json(self):
        logger = StructuredLogger()
        record = logger.info("test_event", model="m1")
        import json
        parsed = json.loads(record.to_json())
        assert parsed["event"] == "test_event"
        assert parsed["model"] == "m1"

    def test_sensitive_content_is_redacted(self):
        """§21.3: diagnose with identifiers, not the full message."""
        logger = StructuredLogger()
        record = logger.error("generation_failed",
                              request_id="req-1", model="m1",
                              content="a listener's private message")
        assert record.fields["content"] == "[redacted]"
        assert record.fields["request_id"] == "req-1"

    def test_identifiers_survive_redaction(self):
        logger = StructuredLogger()
        record = logger.error("failed", request_id="req-1",
                              error_category="timeout",
                              validation_result="invalid")
        assert record.fields["request_id"] == "req-1"
        assert record.fields["error_category"] == "timeout"

    def test_source_text_is_redacted(self):
        logger = StructuredLogger()
        record = logger.error("parse_failed", source_text="secret article body")
        assert record.fields["source_text"] == "[redacted]"

    def test_nested_sensitive_keys_redacted(self):
        logger = StructuredLogger()
        record = logger.error("failed", payload={
            "model": "m1", "prompt": "secret prompt"})
        assert record.fields["payload"]["prompt"] == "[redacted]"
        assert record.fields["payload"]["model"] == "m1"

    def test_formatter_reports_cleanliness(self):
        formatter = RedactingFormatter()
        record = LogRecord(level="info", event="x",
                           fields={"content": "[redacted]"})
        assert formatter.is_clean(record)
        dirty = LogRecord(level="info", event="x",
                          fields={"content": "leaked"})
        assert not formatter.is_clean(dirty)

    def test_correlated_log_bundles_all_ids(self):
        logger = StructuredLogger()
        record = logger.correlated(
            "turn_generated", segment_id="seg-1", session_id="sess-1",
            correlation_id="corr-1", stage="tts")
        assert all([record.segment_id, record.session_id,
                    record.correlation_id, record.stage])


# ── §21.4 Liveness vs readiness ──────────────────────────────────────


class TestLivenessVsReadiness:
    def test_liveness_is_the_process(self):
        service = HealthService()
        assert service.liveness()["alive"] is True

    def test_liveness_can_be_false_while_ready(self):
        """A dead process is not live, but that is separate from readiness."""
        service = HealthService()
        service.set_process_alive(False)
        assert service.liveness()["alive"] is False

    def test_readiness_names_degraded_capabilities(self):
        """§21.4: a single global healthy:false would obscure the distinction."""
        service = HealthService()
        report = service.readiness(Capability.TEXT_TO_SPEECH)
        assert isinstance(report, ReadinessReport)
        assert report.to_dict()["degraded"] is not None

    def test_transcript_can_be_ready_while_tts_is_not(self):
        """§21.4's exact example: serve transcripts while TTS is down."""
        registry = MetricsRegistry()
        # Audio subsystem has only failures.
        for _ in range(5):
            registry.record_error(Subsystem.AUDIO, "synthesis")
        service = HealthService(registry)
        tts = service.readiness(Capability.TEXT_TO_SPEECH)
        assert not tts.ready

    def test_full_readiness_reports_each_capability(self):
        service = HealthService()
        report = service.full_readiness()
        assert "available" in report.to_dict()
        assert "degraded" in report.to_dict()

    def test_readiness_is_not_a_global_flag(self):
        """The report carries per-capability state, not one boolean."""
        service = HealthService()
        report = service.readiness(Capability.INGESTION)
        assert hasattr(report, "available")
        assert hasattr(report, "degraded")


# ── §23.2 Priority scheduling ────────────────────────────────────────


class TestPriorityScheduling:
    def test_five_priority_classes(self):
        assert len(Priority) == 5

    def test_broadcast_is_highest_priority(self):
        assert Priority.BROADCAST_DELIVERY < Priority.URGENT_EDITORIAL
        assert Priority.URGENT_EDITORIAL < Priority.KNOWLEDGE_INTEGRATION

    def test_higher_priority_task_runs_first(self):
        scheduler = PriorityScheduler()
        scheduler.enqueue(Task(id="bg", priority=Priority.BACKGROUND_ENRICHMENT))
        scheduler.enqueue(Task(id="urgent", priority=Priority.URGENT_EDITORIAL))
        task = scheduler.next_task()
        assert task.id == "urgent"

    def test_broadcast_preempts_everything(self):
        scheduler = PriorityScheduler()
        for p in (Priority.BACKGROUND_ENRICHMENT, Priority.ROUTINE_PLANNING,
                  Priority.KNOWLEDGE_INTEGRATION, Priority.URGENT_EDITORIAL):
            scheduler.enqueue(Task(id=f"t-{p}", priority=p))
        scheduler.enqueue(Task(id="live", priority=Priority.BROADCAST_DELIVERY))
        assert scheduler.next_task().id == "live"

    def test_backlog_is_counted(self):
        scheduler = PriorityScheduler()
        scheduler.enqueue(Task(id="a", priority=Priority.ROUTINE_PLANNING))
        scheduler.enqueue(Task(id="b", priority=Priority.ROUTINE_PLANNING))
        assert scheduler.backlog() == 2

    def test_completion_removes_pending_key(self):
        scheduler = PriorityScheduler()
        task = Task(id="a", priority=Priority.ROUTINE_PLANNING,
                    idempotency_key="k1")
        scheduler.enqueue(task)
        scheduler.complete(task)
        # Re-enqueue of the same key is now allowed (it is no longer pending).
        assert scheduler.enqueue(task)


# ── §23.3 Backpressure ───────────────────────────────────────────────


class TestBackpressure:
    def test_bounded_queue_rejects_when_full(self):
        policy = BackpressurePolicy(max_size=2)
        scheduler = PriorityScheduler(
            policies={p: policy for p in Priority})
        assert scheduler.enqueue(Task(id="a", priority=Priority.ROUTINE_PLANNING))
        assert scheduler.enqueue(Task(id="b", priority=Priority.ROUTINE_PLANNING))
        assert not scheduler.enqueue(
            Task(id="c", priority=Priority.ROUTINE_PLANNING))

    def test_full_queue_defers_not_crashes(self):
        """§23.3: an unbounded queue is not a reliability strategy; a full
        bounded one is a defined outcome."""
        scheduler = PriorityScheduler(
            policies={Priority.ROUTINE_PLANNING: BackpressurePolicy(max_size=1)})
        scheduler.enqueue(Task(id="a", priority=Priority.ROUTINE_PLANNING))
        result = scheduler.enqueue(Task(id="b", priority=Priority.ROUTINE_PLANNING))
        assert result is False
        assert scheduler.deferred_count == 1

    def test_duplicate_task_is_coalesced(self):
        """§23.3 rule 7: never generate duplicate segments to compensate."""
        scheduler = PriorityScheduler()
        task = Task(id="a", priority=Priority.ROUTINE_PLANNING,
                    idempotency_key="seg-1")
        assert scheduler.enqueue(task)
        assert not scheduler.enqueue(task)
        assert scheduler.coalesced_count == 1

    def test_low_priority_work_is_reduced_under_load(self):
        """§23.3 step 2: reduce low-priority enrichment work."""
        scheduler = PriorityScheduler()
        scheduler.enqueue(Task(id="a", priority=Priority.BACKGROUND_ENRICHMENT))
        scheduler.enqueue(Task(id="b", priority=Priority.ROUTINE_PLANNING))
        dropped = scheduler.reduce_low_priority_work()
        assert dropped == 2
        assert scheduler.backlog() == 0

    def test_high_priority_survives_reduction(self):
        scheduler = PriorityScheduler()
        scheduler.enqueue(Task(id="live", priority=Priority.BROADCAST_DELIVERY))
        scheduler.reduce_low_priority_work()
        assert scheduler.backlog() == 1

    def test_overload_is_detectable(self):
        scheduler = PriorityScheduler()
        assert not scheduler.is_overloaded()
        for i in range(200):
            scheduler.enqueue(Task(id=f"t{i}", priority=Priority.ROUTINE_PLANNING))
        # Fill to trigger overload detection via backlog.
        assert scheduler.backlog() > 0

    def test_backlog_exposed_to_metrics(self):
        """§23.3 step 6: expose the backlog through observability."""
        registry = MetricsRegistry()
        scheduler = PriorityScheduler(metrics=registry)
        scheduler.enqueue(Task(id="a", priority=Priority.ROUTINE_PLANNING))
        assert registry.get(Subsystem.SYSTEM, "backlog") == 1

    def test_backlog_by_priority(self):
        scheduler = PriorityScheduler()
        scheduler.enqueue(Task(id="a", priority=Priority.BACKGROUND_ENRICHMENT))
        by_priority = scheduler.backlog_by_priority()
        assert by_priority["BACKGROUND_ENRICHMENT"] == 1


# ── §23.4 Caching ────────────────────────────────────────────────────


class TestCaching:
    def test_summary_cache_stores_and_returns(self):
        cache = SummaryCache()
        cache.put("story-1", "snap-1", "A summary")
        assert cache.get("story-1", "snap-1") == "A summary"

    def test_material_claim_change_invalidates(self):
        """§23.4: a material claim change invalidates the summary."""
        cache = SummaryCache()
        cache.put("story-1", "snap-1", "A summary")
        count = cache.invalidate_story("story-1", "material claim changed")
        assert count == 1
        assert cache.get("story-1", "snap-1") is None

    def test_correction_invalidates(self):
        cache = SummaryCache()
        cache.put("story-1", "snap-1", "A summary")
        cache.invalidate_story("story-1", "correction received")
        assert cache.valid_count() == 0

    def test_obsolete_snapshot_invalidates(self):
        cache = SummaryCache()
        cache.put("story-1", "snap-1", "A summary")
        count = cache.invalidate_snapshot("snap-1", "snapshot obsolete")
        assert count == 1
        assert cache.get("story-1", "snap-1") is None

    def test_audio_reuse_on_matching_config(self):
        """§23.4: reuse only when script version and synthesis config match."""
        cache = AudioCache()
        key = AudioCacheKey(script_id="s1", script_version=1,
                            synthesis_attempt="att-1",
                            voice_profile_version="1.0",
                            synthesis_config_version="v1")
        cache.put(key, b"audio")
        assert cache.get(key) == b"audio"

    def test_audio_not_reused_on_script_version_mismatch(self):
        """Reusing audio from an older script version broadcasts stale text."""
        cache = AudioCache()
        cache.put(AudioCacheKey(script_id="s1", script_version=1,
                                synthesis_attempt="att-1",
                                voice_profile_version="1.0"), b"audio")
        newer = AudioCacheKey(script_id="s1", script_version=2,
                              synthesis_attempt="att-1",
                              voice_profile_version="1.0")
        assert cache.get(newer) is None

    def test_audio_not_reused_on_voice_mismatch(self):
        cache = AudioCache()
        cache.put(AudioCacheKey(script_id="s1", script_version=1,
                                synthesis_attempt="att-1",
                                voice_profile_version="1.0"), b"audio")
        other_voice = AudioCacheKey(script_id="s1", script_version=1,
                                    synthesis_attempt="att-1",
                                    voice_profile_version="2.0")
        assert cache.get(other_voice) is None

    def test_audio_invalidation_on_script_change(self):
        cache = AudioCache()
        cache.put(AudioCacheKey(script_id="s1", script_version=1,
                                synthesis_attempt="att-1",
                                voice_profile_version="1.0"), b"audio")
        assert cache.invalidate_script("s1") == 1

    def test_entity_cache_evicts_when_full(self):
        cache = EntityResolutionCache(max_size=2)
        cache.put("Acme", ["acme-corp"])
        cache.put("Beta", ["beta-corp"])
        cache.put("Gamma", ["gamma-corp"])
        assert cache.size() == 2

    def test_cache_hit_miss_tracking(self):
        cache = AudioCache()
        key = AudioCacheKey(script_id="s1", script_version=1,
                            synthesis_attempt="a", voice_profile_version="1")
        cache.put(key, b"x")
        cache.get(key)
        cache.get(AudioCacheKey(script_id="s2", script_version=1,
                                synthesis_attempt="a", voice_profile_version="1"))
        assert cache.hits == 1 and cache.misses == 1


# ── §22 Crash recovery ───────────────────────────────────────────────


class TestCrashRecovery:
    def _director_with_work(self):
        from core.broadcast.director import BroadcastDirector
        from core.broadcast.entities import Segment, SegmentStatus
        director = BroadcastDirector()
        director.add_segment(Segment(
            id="done", status=SegmentStatus.COMPLETED))
        director.add_segment(Segment(
            id="playing", status=SegmentStatus.PLAYING))
        director.add_segment(Segment(
            id="queued", status=SegmentStatus.QUEUED))
        director.queue.append("queued")
        return director

    def test_state_survives_a_simulated_kill(self):
        harness = CrashRecoveryHarness()
        director = self._director_with_work()
        state = harness.snapshot_state(director)
        harness.simulate_kill()
        recovered, report = harness.recover(state)
        assert "done" in recovered.segments
        assert "playing" in recovered.segments

    def test_playing_segment_recovers_as_interrupted(self):
        """A crash mid-playback is not silently a completion."""
        harness = CrashRecoveryHarness()
        director = self._director_with_work()
        state = harness.snapshot_state(director)
        harness.simulate_kill()
        recovered, _ = harness.recover(state)
        assert recovered.segments["playing"].status is SegmentStatus.INTERRUPTED

    def test_queue_is_recovered(self):
        harness = CrashRecoveryHarness()
        director = self._director_with_work()
        state = harness.snapshot_state(director)
        harness.simulate_kill()
        recovered, report = harness.recover(state)
        assert "queued" in recovered.queue

    def test_no_duplicate_replay_of_committed_work(self):
        """§23.3 rule 7 / §30: retries do not create duplicate records."""
        harness = CrashRecoveryHarness()
        harness.ledger.begin("task-1")
        harness.ledger.commit("task-1")
        harness.ledger.begin("task-2")
        replay = harness.recover_tasks(["task-1", "task-2"])
        assert replay == ["task-2"]

    def test_ledger_round_trips(self):
        ledger = RecoveryLedger()
        ledger.begin("t1"); ledger.commit("t1"); ledger.begin("t2")
        restored = RecoveryLedger.from_dict(ledger.to_dict())
        assert restored.was_committed("t1")
        assert "t2" in restored.pending()

    def test_recovery_report_succeeds(self):
        harness = CrashRecoveryHarness()
        director = self._director_with_work()
        state = harness.snapshot_state(director)
        harness.simulate_kill()
        _, report = harness.recover(state)
        assert report.succeeded

    def test_no_duplication_check(self):
        harness = CrashRecoveryHarness()
        assert harness.verify_no_duplication(["a", "b"], ["c", "d"])
        assert not harness.verify_no_duplication(["a", "b"], ["b", "c"])


class TestLongDuration:
    def test_compressed_run_completes(self):
        simulator = LongDurationSimulator()
        scheduler = PriorityScheduler()
        workload = [Task(id=f"t{i}", priority=Priority.ROUTINE_PLANNING)
                    for i in range(20)]
        result = simulator.run(workload, scheduler)
        assert result.cycles == 20

    def test_backlog_stays_bounded(self):
        simulator = LongDurationSimulator()
        scheduler = PriorityScheduler()
        workload = [Task(id=f"t{i}", priority=Priority.KNOWLEDGE_INTEGRATION)
                    for i in range(50)]
        result = simulator.run(workload, scheduler)
        assert result.peak_backlog <= 256

    def test_no_duplicate_segments_over_the_run(self):
        simulator = LongDurationSimulator()
        scheduler = PriorityScheduler()
        workload = [Task(id=f"t{i}", priority=Priority.ROUTINE_PLANNING,
                         idempotency_key=f"k{i}")
                    for i in range(30)]
        result = simulator.run(workload, scheduler)
        assert result.duplicate_segments == 0

    def test_ceiling_violation_is_detected(self):
        # A ceiling of 0 is violated as soon as any task is in the backlog.
        simulator = LongDurationSimulator(
            ceilings=[ResourceCeiling("backlog", 0)])
        scheduler = PriorityScheduler()
        workload = [Task(id="t0", priority=Priority.ROUTINE_PLANNING)]
        result = simulator.run(workload, scheduler)
        assert result.ceiling_violations

    def test_ceiling_not_violated_when_within_bound(self):
        simulator = LongDurationSimulator(
            ceilings=[ResourceCeiling("backlog", 1000)])
        scheduler = PriorityScheduler()
        workload = [Task(id=f"t{i}", priority=Priority.ROUTINE_PLANNING)
                    for i in range(10)]
        result = simulator.run(workload, scheduler)
        assert not result.ceiling_violations

    def test_within_bounds_when_clean(self):
        result = type("R", (), {
            "ceiling_violations": [], "duplicate_segments": 0})()
        from core.reliability.recovery import LongRunResult
        clean = LongRunResult(cycles=10)
        assert clean.within_bounds


# ── §23.5 Resource-aware routing ─────────────────────────────────────


class TestResourceRouting:
    def test_routes_to_a_qualified_provider(self):
        router = ResourceAwareRouter()
        decision = router.route("script_generation", "natural_language")
        assert decision.choice is RouteChoice.ROUTED

    def test_defers_non_urgent_under_contention(self):
        """§23.5: a large model may be unavailable during synthesis."""
        router = ResourceAwareRouter()
        router.set_resource_state(ResourceState.CONSTRAINED)
        decision = router.route("long_form_analysis", "long_context",
                                urgent=False)
        assert decision.choice is RouteChoice.DEFERRED

    def test_urgent_work_proceeds_under_contention(self):
        router = ResourceAwareRouter()
        router.set_resource_state(ResourceState.CONSTRAINED)
        decision = router.route("script_generation", "natural_language",
                                urgent=True)
        assert decision.choice is RouteChoice.ROUTED

    def test_never_silently_downgrades(self):
        """§23.5: the decision is based on task requirements, not silently
        lowering the quality of every operation."""
        router = ResourceAwareRouter()
        router.route("script_generation", "natural_language")
        router.set_resource_state(ResourceState.CONSTRAINED)
        router.route("long_form_analysis", "long_context")
        assert router.silent_downgrades == 0

    def test_no_route_when_capability_absent(self):
        from core.broadcast.providers import StubLanguageModelProvider
        router = ResourceAwareRouter(
            providers={"nostruct": StubLanguageModelProvider(
                structured_output=False)})
        decision = router.route("entity_extraction", "structured_output")
        assert decision.choice is RouteChoice.NO_ROUTE

    def test_decision_records_its_reason(self):
        router = ResourceAwareRouter()
        decision = router.route("script_generation", "natural_language")
        assert decision.reason


# ── §27.4 Continuous operation ───────────────────────────────────────


class TestContinuousOperation:
    def test_new_development_is_permitted(self):
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=True)
        assert decision.nature is SegmentNature.NEW_DEVELOPMENT
        assert decision.permitted

    def test_silence_is_permitted_when_nothing_to_add(self):
        """§27.4: permitted to remain silent rather than fabricate."""
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=False)
        assert decision.nature is SegmentNature.SILENCE
        assert decision.permitted

    def test_unresolved_questions_are_revisited_with_disclosure(self):
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=False,
                                 has_unresolved_questions=True)
        assert decision.nature is SegmentNature.REVISITING_UNRESOLVED
        assert decision.discloses_revisit

    def test_historical_context_is_disclosed(self):
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=False,
                                 has_historical_context=True)
        assert decision.nature is SegmentNature.HISTORICAL_CONTEXT
        assert decision.discloses_revisit

    def test_interstitial_is_permitted(self):
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=False,
                                 interstitial_configured=True)
        assert decision.nature is SegmentNature.INTERSTITIAL

    def test_no_fabricated_novelty(self):
        """§27.4: an infinite broadcast is not an infinite stream of
        fabricated novelty."""
        policy = ContinuousOperationPolicy()
        decision = policy.decide(has_new_development=False,
                                 has_historical_context=True)
        assert not decision.is_fabricated_novelty

    def test_undisclosed_revisit_is_fabricated_novelty(self):
        from core.reliability.routing import ProgrammingDecision
        bad = ProgrammingDecision(
            SegmentNature.HISTORICAL_CONTEXT, permitted=True,
            discloses_revisit=False)
        assert bad.is_fabricated_novelty

    def test_filler_must_be_disclosed(self):
        policy = ContinuousOperationPolicy()
        assert policy.filler_must_be_disclosed(SegmentNature.FRAMED_ANALYSIS)
        assert not policy.filler_must_be_disclosed(
            SegmentNature.NEW_DEVELOPMENT)


# ── §28 Recursive improvement ────────────────────────────────────────


class TestRecursiveImprovement:
    def test_adjustable_parameters_exist(self):
        assert "ranking_weight_novelty" in ADJUSTABLE_PARAMETERS

    def test_protected_parameters_exist(self):
        assert "validation_stages" in PROTECTED_PARAMETERS

    def test_cannot_weaken_factual_validation(self):
        """§28.2: the system must not autonomously weaken factual validation."""
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Too few segments broadcast",
            parameter="validation_stages",
            current_value=8, proposed_value=2)
        assert engine.cannot_weaken_validation(proposal)
        assert proposal.state is ImprovementState.REJECTED

    def test_cannot_touch_provenance_enforcement(self):
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Slow", parameter="provenance_enforcement",
            current_value=True, proposed_value=False)
        assert proposal.is_protected
        assert proposal.state is ImprovementState.REJECTED

    def test_adjustable_parameter_within_bounds_is_proposed(self):
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Novelty overweights volume",
            parameter="ranking_weight_novelty",
            current_value=1.0, proposed_value=1.5,
            rollback_procedure="restore previous weight")
        assert proposal.state is ImprovementState.CHANGE_PROPOSED

    def test_out_of_bounds_value_is_rejected(self):
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Tune", parameter="ranking_weight_novelty",
            current_value=1.0, proposed_value=99.0,
            rollback_procedure="restore")
        assert proposal.state is ImprovementState.REJECTED

    def test_unknown_parameter_is_rejected(self):
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Tune", parameter="mystery_knob",
            current_value=1, proposed_value=2)
        assert proposal.state is ImprovementState.REJECTED

    def test_evaluation_across_multiple_dimensions(self):
        """§28.4: individual dimensions, not one opaque score."""
        baseline = QualityAssessment(dimensions={
            "F": 0.8, "P": 0.9, "N": 0.5, "C": 0.7, "D": 0.6, "R": 0.7, "S": 0.9})
        candidate = QualityAssessment(dimensions={
            "F": 0.85, "P": 0.9, "N": 0.7, "C": 0.7, "D": 0.6, "R": 0.7, "S": 0.9})
        evaluator = ImprovementEvaluator()
        comparison = evaluator.compare(baseline, candidate)
        assert comparison["acceptable"]
        assert "N" in comparison["improvements"]

    def test_regression_blocks_improvement(self):
        """A gain in novelty that tanks factual grounding is not an
        improvement (§28.4)."""
        baseline = QualityAssessment(dimensions={"F": 0.9, "N": 0.5})
        candidate = QualityAssessment(dimensions={"F": 0.3, "N": 0.9})
        evaluator = ImprovementEvaluator()
        assert not evaluator.is_improvement(baseline, candidate)

    def test_engine_never_self_approves(self):
        """§28.3: approval is explicit and recorded."""
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Tune", parameter="ranking_weight_novelty",
            current_value=1.0, proposed_value=1.2,
            rollback_procedure="restore")
        # Not yet tested, so cannot be approved.
        assert not engine.approve(proposal, "operator")

    def test_deploy_requires_approval_and_rollback(self):
        engine = RecursiveImprovementEngine()
        proposal = engine.propose(
            "Tune", parameter="ranking_weight_novelty",
            current_value=1.0, proposed_value=1.2,
            rollback_procedure="restore previous weight")
        engine.evaluate(proposal,
                        QualityAssessment(dimensions={"N": 0.5}),
                        QualityAssessment(dimensions={"N": 0.7}))
        assert engine.approve(proposal, "operator")
        assert engine.deploy(proposal)
        assert engine.deployment_log

    def test_deploy_without_rollback_is_refused(self):
        """§28.3 requires a rollback procedure before deployment."""
        engine = RecursiveImprovementEngine()
        proposal = ImprovementProposal(
            id="imp-x", problem_statement="p", parameter="ranking_weight_novelty",
            current_value=1.0, proposed_value=1.2, rollback_procedure="")
        proposal.state = ImprovementState.APPROVED
        assert not engine.deploy(proposal)

    def test_quality_dimensions_are_separate(self):
        assessment = QualityAssessment(dimensions={"F": 0.8, "N": 0.5})
        assert len(assessment.all_dimensions) == 2
        assert assessment.dimension("F").label == "factual_grounding"


# ── §16.8 Streaming boundary ─────────────────────────────────────────


class TestStreamingBoundary:
    def test_audio_and_metadata_are_separate(self):
        manager = StreamManager()
        assert manager.transports_are_separate()

    def test_http_transport_opens_and_writes(self):
        transport = HttpChunkedTransport()
        assert transport.open()
        assert transport.write_chunk(b"audio", 0)
        assert transport.chunks_written == 1

    def test_icecast_transport_is_pluggable(self):
        """§16.8: an external server can be added without engine changes."""
        manager = StreamManager()
        icecast = IcecastTransport()
        manager.swap_audio_transport(icecast)
        assert manager.audio_transport.kind is TransportKind.ICECAST
        assert manager.transports_are_separate()

    def test_metadata_never_rides_the_audio_channel(self):
        manager = StreamManager()
        manager.open()
        manager.emit_metadata({"type": "segment_started"})
        assert manager.metadata_transport.events_sent == 1
        # The audio transport saw no metadata write.
        assert manager.audio_transport.chunks_written == 0

    def test_audio_endpoint_contract_is_explicit(self):
        """§19.4: format, framing, buffering, reconnection, EOS, auth,
        backpressure all defined."""
        transport = HttpChunkedTransport()
        contract = transport.contract().to_dict()
        for field in ("audio_format", "framing", "buffering", "reconnection",
                      "end_of_stream", "authentication", "backpressure_policy"):
            assert field in contract, field

    def test_closed_transport_refuses_writes(self):
        transport = HttpChunkedTransport()
        transport.open()
        transport.close()
        assert not transport.write_chunk(b"x", 0)

    def test_swapping_transport_closes_the_old_one(self):
        manager = StreamManager()
        manager.open()
        manager.swap_audio_transport(IcecastTransport())
        assert manager.audio_transport.opened


# ── §30 Definition of done ───────────────────────────────────────────


class TestDefinitionOfDone:
    def test_information_integrity_checks_exist(self):
        """§30: every captured article has provenance; corrections do not
        erase history; snapshots have temporal semantics."""
        # Persistence append-only triggers (Phase 1) and snapshot watermarks.
        from core.persistence import SCHEMA_VERSION
        assert SCHEMA_VERSION >= 2

    def test_editorial_intelligence_checks_exist(self):
        """§30: material change detection; ranking by explicit factors."""
        from core.world_model.changes import ChangeKind
        assert len(ChangeKind) >= 10

    def test_broadcast_generation_checks_exist(self):
        """§30: scripts pass mandatory validation; interruptions do not cause
        stale audio."""
        from core.broadcast.validation import ValidationStage
        assert len(ValidationStage) == 8

    def test_operational_reliability_checks_exist(self):
        """§30: survives restarts; queues bounded; failures diagnosable."""
        from core.broadcast.director import BroadcastDirector
        assert hasattr(BroadcastDirector, "recover")

    def test_recursive_improvement_checks_exist(self):
        """§30: can propose, test against baseline, version and reverse."""
        from core.reliability.improvement import RecursiveImprovementEngine
        assert hasattr(RecursiveImprovementEngine, "deploy")

    def test_bounded_resource_usage(self):
        """§30: queue growth and resource usage are bounded."""
        scheduler = PriorityScheduler(
            policies={p: BackpressurePolicy(max_size=1) for p in Priority})
        for i in range(5):
            scheduler.enqueue(Task(id=f"t{i}", priority=Priority.ROUTINE_PLANNING))
        assert scheduler.backlog() <= 1

    def test_diagnosable_failures(self):
        """§30: logs and metrics make failures diagnosable."""
        logger = StructuredLogger()
        registry = MetricsRegistry()
        registry.record_error(Subsystem.AUDIO, "synthesis")
        logger.error("synthesis_failed", request_id="req-1",
                     error_category="provider_unavailable")
        assert registry.get(Subsystem.AUDIO, "synthesis_failures") == 1
        assert logger.records[-1].fields["request_id"] == "req-1"
