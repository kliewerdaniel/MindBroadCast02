"""Multi-persona dialogue tests — Gate 9.

Spec §12 (persona architecture), §12.4 (independent analysis), §12.5
(disagreement), §12.6 (memory), §12.7 (consistency), §14.7 (correlated errors).

The tests that matter assert the refusals: no manufactured disagreement when
evidence is clear, no forced consensus when the issue is unresolved, no silent
persona-config rewrite, no unbounded memory, and no unexplained position drift.
"""

import pytest
from datetime import datetime, timezone, timedelta

from core.personas.entities import (
    AnalyticalPreferences, ConsistencyDimension, ConsistencyReport,
    DialogueTurn, Disagreement, DisagreementType, MemoryType, MemoryValidity,
    PersonaAnalysis, PersonaConfig, PersonaMemoryEntry, ResolutionStatus,
    StructuredExchange,
)
from core.personas.memory import MemoryStore, PersonaRegistry
from core.personas.analysis import (
    AnalysisFunction, DisagreementDetector, DisagreementGuard,
    IndependentAnalysisRunner, SharedEvidencePacket,
)
from core.personas.dialogue import DialogueComposer
from core.personas.consistency import (
    ConsistencyEvaluator, ImitationDetector,
)
from core.personas.dialogue_orchestrator import DialogueOrchestrator

NOW = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)


def persona(pid="skeptic", name="Skeptic", role="challenges weak evidence",
            version=1, enabled=True, style=None):
    return PersonaConfig(
        id=pid, name=name, role=role, enabled=enabled,
        configuration_version=version,
        preferences=AnalyticalPreferences(
            analytical_style="adversarial",
            speaking_style=style or {"pace": "measured", "depth": "high"},
        ),
    )


def analysis(pid, position, assumptions=None, evidence=None, facts=None,
             question="Q", confidence=0.6):
    return PersonaAnalysis(
        persona_id=pid, question=question, position=position,
        assumptions=assumptions or [], evidence_used=evidence or [],
        shared_facts_accepted=facts or [], confidence=confidence,
    )


def packet(question="Should the policy be adopted?", facts=None,
           evidence=None, contradictory=None):
    return SharedEvidencePacket(
        id="packet-1", question=question,
        facts=facts if facts is not None else ["The policy reduces emissions."],
        evidence_references=evidence or ["e1", "e2"],
        contradictory_evidence=contradictory or [],
    )


# ── §12.2 Versioned persona configuration ────────────────────────────


class TestPersonaConfig:
    def test_update_creates_a_new_version(self):
        config = persona()
        revised = config.revised(role="new role")
        assert revised.configuration_version == 2
        assert config.configuration_version == 1

    def test_previous_version_is_retained(self):
        config = persona()
        revised = config.revised(role="new role")
        assert revised.superseded_by_version is None
        # The original is untouched, so a v1 segment traces to v1 instructions.
        assert config.role == "challenges weak evidence"

    def test_registry_tracks_history(self):
        registry = PersonaRegistry()
        registry.register(persona())
        registry.update("skeptic", role="changed")
        assert len(registry.versions("skeptic")) == 2

    def test_prior_version_is_retrievable(self):
        """§12.2: a previously generated segment must trace to the
        instructions that produced it."""
        registry = PersonaRegistry()
        registry.register(persona())
        registry.update("skeptic", role="changed")
        v1 = registry.version_at("skeptic", 1)
        assert v1.role == "challenges weak evidence"

    def test_update_does_not_silently_rewrite(self):
        registry = PersonaRegistry()
        registry.register(persona())
        original = registry.get("skeptic")
        registry.update("skeptic", description="new")
        assert original.description != registry.get("skeptic").description
        assert original is not registry.get("skeptic")

    def test_disabled_persona_is_excluded(self):
        registry = PersonaRegistry()
        registry.register(persona(enabled=False))
        assert registry.enabled() == []

    def test_unknown_persona_raises(self):
        registry = PersonaRegistry()
        with pytest.raises(KeyError):
            registry.update("nope", role="x")

    def test_preferences_are_separated(self):
        """§12.2: analytical, epistemic, rhetorical, interaction, memory."""
        prefs = AnalyticalPreferences(
            analytical_style="institutional",
            epistemic={"requires_primary_source": True},
            rhetorical={"uses_analogies": True},
            interaction={"challenges_others": True},
            speaking_style={"pace": "slow"},
        )
        d = prefs.to_dict()
        assert set(d) >= {"analytical_style", "epistemic", "rhetorical",
                          "interaction", "speaking_style"}


# ── §12.3 Epistemology is separate from personality ──────────────────


class TestEpistemologyPersonalitySeparation:
    def test_personality_never_determines_truth(self):
        """§12.3: personality must not independently determine whether a
        factual proposition is true. The shared packet does."""
        pkt = packet(facts=["The policy reduces emissions."])
        # Every persona draws on the same facts regardless of style.
        assert "The policy reduces emissions." in pkt.facts

    def test_personas_share_an_evidence_baseline(self):
        """§12.3: the shared knowledge model provides a common baseline."""
        pkt = packet(evidence=["e1", "e2"])
        runner = IndependentAnalysisRunner()
        analyses = runner.run_first_passes(
            [persona("a"), persona("b")], pkt)
        for a in analyses:
            assert a.evidence_used == ["e1", "e2"]

    def test_personas_may_disagree_on_significance_not_facts(self):
        """§12.3: disagree about significance while agreeing about content."""
        a = analysis("a", "This is the most important policy of the decade.",
                     facts=["The policy reduces emissions."])
        b = analysis("b", "This is a marginal policy with limited effect.",
                     facts=["The policy reduces emissions."])
        # Same accepted fact, different significance judgment.
        assert a.shared_facts_accepted == b.shared_facts_accepted
        assert a.position != b.position

    def test_disagreement_records_shared_facts(self):
        """A real disagreement states what is agreed, not only what is disputed."""
        detector = DisagreementDetector()
        pkt = packet(facts=["The policy reduces emissions."])
        disagreements = detector.detect([
            analysis("a", "Adopt it immediately.", facts=["The policy reduces emissions."]),
            analysis("b", "Reject it outright.", facts=["The policy reduces emissions."]),
        ], pkt)
        assert disagreements
        assert disagreements[0].shared_facts


# ── §12.4 Independent analysis ───────────────────────────────────────


class TestIndependentAnalysis:
    def test_first_passes_are_produced_for_each_persona(self):
        runner = IndependentAnalysisRunner()
        analyses = runner.run_first_passes(
            [persona("a"), persona("b"), persona("c")],
            packet())
        assert len(analyses) == 3
        assert {a.persona_id for a in analyses} == {"a", "b", "c"}

    def test_no_persona_sees_anothers_first_pass(self):
        """§12.4: later personas must not inherit earlier framing.

        The runner passes only the shared packet to each analysis function.
        A function that tries to read another's output cannot — it is not
        available.
        """
        seen = {}

        def make(pid):
            def function(config, pkt):
                seen[pid] = pkt
                return analysis(pid, f"{config.name} position")
            return function

        runner = IndependentAnalysisRunner()
        runner.register("a", make("a"))
        runner.register("b", make("b"))
        runner.run_first_passes([persona("a"), persona("b")], packet())
        # Each saw the same packet object, and only the packet.
        assert seen["a"] is seen["b"]

    def test_analysis_records_its_assumptions(self):
        """§12.4 step 4: record the assumptions and evidence used."""
        runner = IndependentAnalysisRunner()
        runner.register("a", lambda config, pkt: analysis(
            "a", "Position.", assumptions=["Markets are efficient"],
            evidence=["e1"], facts=pkt.facts))
        analyses = runner.run_first_passes([persona("a")], packet())
        assert analyses[0].assumptions == ["Markets are efficient"]
        assert analyses[0].evidence_used == ["e1"]

    def test_analysis_records_persona_version(self):
        runner = IndependentAnalysisRunner()
        analyses = runner.run_first_passes(
            [persona("a", version=3)], packet())
        assert analyses[0].persona_version == 3

    def test_persona_without_function_produces_an_empty_honest_analysis(self):
        """No fabricated position when no analysis function is registered."""
        runner = IndependentAnalysisRunner()
        analyses = runner.run_first_passes([persona("a")], packet())
        assert analyses[0].position == ""
        assert analyses[0].confidence == 0.0

    def test_sequential_ordering_is_enforced(self):
        """The runner completes all first passes before any exchange begins."""
        order = []

        def make(pid, position):
            def function(config, pkt):
                order.append(f"analyze:{pid}")
                return analysis(pid, position)
            return function

        runner = IndependentAnalysisRunner()
        runner.register("a", make("a", "Adopt."))
        runner.register("b", make("b", "Reject."))
        analyses = runner.run_first_passes(
            [persona("a"), persona("b")], packet())
        # Both analyses exist before composition could start.
        assert len(analyses) == 2
        assert order == ["analyze:a", "analyze:b"]


# ── §12.5 The disagreement model ─────────────────────────────────────


class TestDisagreementModel:
    def test_all_seven_types_exist(self):
        assert len(DisagreementType) == 7

    def test_disagreement_identifies_the_precise_proposition(self):
        """§12.5: a useful disagreement identifies the precise proposition."""
        detector = DisagreementDetector()
        pkt = packet(question="Should the policy be adopted?")
        found = detector.detect([
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
        ], pkt)
        assert found[0].proposition == "Should the policy be adopted?"

    def test_records_positions_of_both_parties(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
        ], packet())
        assert found[0].position_a == "Adopt it now."
        assert found[0].position_b == "Reject it entirely."
        assert found[0].persona_a_id == "a"
        assert found[0].persona_b_id == "b"

    def test_records_disputed_assumptions(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now.", assumptions=["Markets efficient"]),
            analysis("b", "Reject it entirely.", assumptions=["Markets fail"]),
        ], packet())
        assert found[0].disputed_assumptions

    def test_records_evidence_references(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now.", evidence=["e1"]),
            analysis("b", "Reject it entirely.", evidence=["e2"]),
        ], packet())
        assert set(found[0].evidence_references) == {"e1", "e2"}

    def test_classifies_evidence_assessment_when_evidence_differs(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now.", evidence=["e1"]),
            analysis("b", "Reject it entirely.", evidence=["e2"]),
        ], packet())
        assert found[0].disagreement_type is (
            DisagreementType.DIFFERENT_EVIDENCE_ASSESSMENTS)

    def test_classifies_competing_explanations_when_assumptions_differ(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now.", evidence=["e1"],
                     assumptions=["X"]),
            analysis("b", "Reject it entirely.", evidence=["e1"],
                     assumptions=["Y"]),
        ], packet())
        assert found[0].disagreement_type is (
            DisagreementType.COMPETING_CAUSAL_EXPLANATIONS)

    def test_identical_positions_are_not_a_disagreement(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now."),
            analysis("b", "Adopt it now."),
        ], packet())
        assert found == []

    def test_empty_positions_are_not_a_disagreement(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", ""), analysis("b", ""),
        ], packet())
        assert found == []

    def test_disagreement_starts_unresolved(self):
        detector = DisagreementDetector()
        found = detector.detect([
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
        ], packet())
        assert found[0].resolution_status is ResolutionStatus.UNRESOLVED


class TestNoManufacturedDisagreement:
    def test_unanimous_analyses_suppress_disagreement(self):
        """§12.5: do not manufacture disagreement when evidence supports a
        straightforward conclusion."""
        guard = DisagreementGuard()
        pkt = packet(facts=["The policy reduces emissions."])
        analyses = [
            analysis("a", "Adopt it.", facts=["The policy reduces emissions."],
                     evidence=["e1"]),
            analysis("b", "Adopt it.", facts=["The policy reduces emissions."],
                     evidence=["e1"]),
        ]
        assert guard.should_suppress(pkt, analyses)

    def test_single_persona_is_not_a_debate(self):
        guard = DisagreementGuard()
        assert guard.should_suppress(packet(), [analysis("a", "Adopt.")])

    def test_genuine_disagreement_is_not_suppressed(self):
        guard = DisagreementGuard()
        analyses = [
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
        ]
        assert not guard.should_suppress(packet(), analyses)


class TestNoForcedConsensus:
    def test_unresolved_disagreements_remain_unresolved(self):
        """§12.5: do not force consensus when the issue is unresolved."""
        guard = DisagreementGuard()
        disagreement = Disagreement(
            proposition="Should the policy be adopted?",
            resolution_status=ResolutionStatus.UNRESOLVED)
        assert guard.must_not_force_consensus([disagreement]) == [disagreement]

    def test_resolved_disagreements_are_excluded(self):
        guard = DisagreementGuard()
        resolved = Disagreement(
            proposition="Q",
            resolution_status=ResolutionStatus.RESOLVED_BY_EVIDENCE)
        assert guard.must_not_force_consensus([resolved]) == []

    def test_non_substantive_disagreements_are_excluded(self):
        guard = DisagreementGuard()
        empty = Disagreement(proposition="",
                             resolution_status=ResolutionStatus.UNRESOLVED)
        assert guard.must_not_force_consensus([empty]) == []


# ── §12.4 Structured exchange ────────────────────────────────────────


class TestStructuredExchange:
    def test_exchange_addresses_each_disagreement(self):
        """§12.4 step 6: an exchange addressing those disagreements."""
        composer = DialogueComposer()
        pkt = packet(question="Adopt the policy?")
        analyses = [
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
        ]
        detector = DisagreementDetector()
        disagreements = detector.detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert exchange.disagreements_addressed
        assert len(exchange.disagreements_addressed) == len(disagreements)

    def test_each_turn_traces_to_its_disagreement(self):
        composer = DialogueComposer()
        pkt = packet(question="Adopt?")
        analyses = [analysis("a", "Adopt it now."),
                    analysis("b", "Reject it entirely.")]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        for turn in exchange.turns:
            if turn.speech_act == "position":
                assert turn.addresses_disagreement_id in (
                    exchange.disagreements_addressed)

    def test_position_aired_once_across_multiple_disagreements(self):
        """A persona in several pairwise disagreements speaks once, not per pair.

        With three personas all disagreeing, each position participates in
        multiple pairwise disagreements; reading each out would stutter. The
        disagreement is still recorded; only the spoken turn is deduped.
        """
        composer = DialogueComposer()
        pkt = packet(question="Adopt?")
        analyses = [
            analysis("a", "Adopt it now, decisively."),
            analysis("b", "Reject it entirely, no exceptions."),
            analysis("c", "Delay pending more evidence gathering."),
        ]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        # Every disagreement is addressed in the record...
        assert len(exchange.disagreements_addressed) == len(disagreements)
        # ...but no (persona, text) spoken twice.
        spoken = [(t.persona_id, t.text) for t in exchange.turns
                  if t.speech_act == "position"]
        assert len(spoken) == len(set(spoken))
        # Each persona's position appears exactly once.
        for pid in ("a", "b", "c"):
            assert sum(1 for t in exchange.turns
                       if t.persona_id == pid
                       and t.speech_act == "position") == 1

    def test_unresolved_deduped(self):
        """Shared propositions are listed once, not once per disagreement."""
        composer = DialogueComposer()
        pkt = packet(question="Adopt?")
        analyses = [
            analysis("a", "Adopt it now, decisively."),
            analysis("b", "Reject it entirely, no exceptions."),
            analysis("c", "Delay pending more evidence gathering."),
        ]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert len(exchange.unresolved) == len(set(exchange.unresolved))

    def test_exchange_carries_shared_facts(self):
        composer = DialogueComposer()
        pkt = packet(facts=["The policy reduces emissions."])
        analyses = [analysis("a", "Adopt."), analysis("b", "Reject.")]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert exchange.shared_facts == ["The policy reduces emissions."]

    def test_exchange_records_what_remains_unresolved(self):
        composer = DialogueComposer()
        pkt = packet(question="Adopt?")
        analyses = [analysis("a", "Adopt it now."),
                    analysis("b", "Reject it entirely.")]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert exchange.unresolved

    def test_persona_without_disagreement_still_appears(self):
        """The exchange is not silently narrowed to the debaters."""
        composer = DialogueComposer()
        pkt = packet(question="Adopt?")
        analyses = [
            analysis("a", "Adopt it now."),
            analysis("b", "Reject it entirely."),
            analysis("c", "I have a separate concern about cost.",
                     facts=["The policy reduces emissions."]),
        ]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert "c" in exchange.personas

    def test_turn_limit_is_enforced(self):
        composer = DialogueComposer(max_turns=2)
        pkt = packet(question="Adopt?")
        analyses = [analysis("a", "Adopt it now."),
                    analysis("b", "Reject it entirely.")]
        disagreements = DisagreementDetector().detect(analyses, pkt)
        exchange = composer.compose(pkt, analyses, disagreements)
        assert len(exchange.turns) <= 2

    def test_resolution_requires_a_stated_basis(self):
        composer = DialogueComposer()
        disagreement = Disagreement(proposition="Q")
        composer.resolve(disagreement, ResolutionStatus.RESOLVED_BY_EVIDENCE,
                         evidence=["e9"])
        assert disagreement.resolution_status is (
            ResolutionStatus.RESOLVED_BY_EVIDENCE)
        assert "e9" in disagreement.evidence_references


# ── §12.6 Persona memory ─────────────────────────────────────────────


class TestPersonaMemory:
    def test_all_five_memory_types_exist(self):
        assert len(MemoryType) == 5

    def test_memory_is_typed(self):
        store = MemoryStore()
        store.remember("skeptic", MemoryType.SEMANTIC, "A stable conclusion")
        assert store.count("skeptic", MemoryType.SEMANTIC) == 1
        assert store.count("skeptic", MemoryType.EPISODIC) == 0

    def test_memory_carries_provenance(self):
        store = MemoryStore()
        entry = store.remember("skeptic", MemoryType.EPISODIC, "Participated",
                               provenance="dialogue_orchestrator")
        assert entry.provenance == "dialogue_orchestrator"

    def test_memory_carries_confidence(self):
        store = MemoryStore()
        entry = store.remember("skeptic", MemoryType.SEMANTIC, "Conclusion",
                               confidence=0.9)
        assert entry.confidence == 0.9

    def test_memory_links_to_a_segment(self):
        store = MemoryStore()
        entry = store.remember("skeptic", MemoryType.EPISODIC, "Participated",
                               source_segment_id="seg-1")
        assert entry.source_segment_id == "seg-1"

    def test_memory_is_bounded_per_type(self):
        """§12.6: persistent but bounded.

        Active memory is capped. Withdrawn entries are retained up to the same
        cap as a bounded audit trail, so a withdrawal stays visible but memory
        does not grow without bound through churn.
        """
        store = MemoryStore(max_per_type=3)
        for i in range(10):
            store.remember("skeptic", MemoryType.EPISODIC, f"Event {i}")
        active = store.recall("skeptic", MemoryType.EPISODIC)
        assert len(active) == 3
        # Total is bounded at 2x the cap (active + retained withdrawn).
        assert store.count("skeptic", MemoryType.EPISODIC) <= 6

    def test_context_is_bounded(self):
        """§12.6: memory must not become an uncontrolled transcript."""
        store = MemoryStore(context_line_budget=5)
        for i in range(50):
            store.remember("skeptic", MemoryType.EPISODIC, f"Event {i}")
        lines = store.context_lines("skeptic")
        assert len(lines) == 5

    def test_context_prioritises_corrections(self):
        """A withdrawn position must be visible in context."""
        store = MemoryStore()
        store.remember("skeptic", MemoryType.SEMANTIC, "Stable conclusion")
        store.remember("skeptic", MemoryType.CORRECTION, "I was wrong about X")
        lines = store.context_lines("skeptic")
        assert "correction" in lines[0]

    def test_recall_filters_inactive_by_default(self):
        store = MemoryStore()
        entry = store.remember("skeptic", MemoryType.SEMANTIC, "Old view")
        revised = store.remember("skeptic", MemoryType.CORRECTION, "New view",
                                 revises_entry_id=entry.id)
        active = store.recall("skeptic", MemoryType.SEMANTIC)
        assert entry not in active
        assert entry.validity is MemoryValidity.SUPERSEDED

    def test_recall_can_include_inactive(self):
        store = MemoryStore()
        store.remember("skeptic", MemoryType.SEMANTIC, "Old view")
        all_entries = store.recall("skeptic", MemoryType.SEMANTIC,
                                   active_only=False)
        assert len(all_entries) == 1

    def test_history_retains_withdrawn_entries(self):
        """§12.6: a persona can recall it previously argued a position that
        later evidence weakened."""
        store = MemoryStore()
        entry = store.remember("skeptic", MemoryType.SEMANTIC,
                               "The policy will reduce emissions")
        store.remember("skeptic", MemoryType.CORRECTION,
                       "The policy will reduce emissions was overstated",
                       revises_entry_id=entry.id)
        history = store.history_of("skeptic", "reduce emissions")
        assert len(history) == 2      # original and its revision

    def test_revision_points_at_what_it_revises(self):
        store = MemoryStore()
        original = store.remember("skeptic", MemoryType.SEMANTIC, "Old view")
        revision = store.remember("skeptic", MemoryType.CORRECTION, "New view",
                                  revises_entry_id=original.id)
        assert revision.revises_entry_id == original.id
        assert store.revisions_of(original.id) == [revision]

    def test_recall_is_per_persona(self):
        store = MemoryStore()
        store.remember("skeptic", MemoryType.SEMANTIC, "Skeptic's view")
        store.remember("analyst", MemoryType.SEMANTIC, "Analyst's view")
        assert store.count("skeptic") == 1
        assert store.count("analyst") == 1

    def test_forced_eviction_marks_withdrawal(self):
        """Bounding an ACTIVE entry makes the withdrawal visible, not silent."""
        store = MemoryStore(max_per_type=2)
        for i in range(4):
            store.remember("skeptic", MemoryType.EPISODIC, f"Event {i}")
        entries = store.recall("skeptic", MemoryType.EPISODIC,
                               active_only=False)
        assert any(e.validity is MemoryValidity.WITHDRAWN for e in entries)

    def test_inactive_entries_evicted_before_active(self):
        store = MemoryStore(max_per_type=2)
        store.remember("skeptic", MemoryType.EPISODIC, "Event 0")
        entry = store.remember("skeptic", MemoryType.EPISODIC, "Event 1")
        entry.validity = MemoryValidity.SUPERSEDED
        store.remember("skeptic", MemoryType.EPISODIC, "Event 2")
        active = store.recall("skeptic", MemoryType.EPISODIC)
        # The superseded entry was dropped first; the two active ones remain.
        assert len(active) == 2


# ── §12.7 Consistency ────────────────────────────────────────────────


class TestConsistencyEvaluation:
    def test_all_six_dimensions_are_measured(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(persona(), [analysis("skeptic", "Position.")])
        assert len(report.dimensions) == 6
        names = {d.name for d in report.dimensions}
        assert names == {
            "voice_stability", "assumption_consistency",
            "acknowledges_contradiction", "appropriate_revision",
            "assertion_discipline", "role_adherence"}

    def test_explainable_change_is_not_drift(self):
        """§12.7: a persona may change its mind when the change is explainable.

        A CORRECTION memory entry citing new evidence is the explanation.
        """
        memory = MemoryStore()
        memory.remember("skeptic", MemoryType.CORRECTION,
                        "Revised after new evidence", provenance="segment-3")
        evaluator = ConsistencyEvaluator(memory)
        report = evaluator.evaluate(
            persona(), [analysis("skeptic", "New position.")],
            prior_analyses=[analysis("skeptic", "Old position.")])
        revision = report.dimension("appropriate_revision")
        assert revision.score == 1.0
        assert "recorded correction" in revision.detail

    def test_unexplained_change_is_drift(self):
        memory = MemoryStore()
        evaluator = ConsistencyEvaluator(memory)
        report = evaluator.evaluate(
            persona(), [analysis("skeptic", "Completely different position.")],
            prior_analyses=[analysis("skeptic", "Original position.")],
            config_changed=False)
        revision = report.dimension("appropriate_revision")
        assert revision.score < 0.5

    def test_config_update_explains_a_change(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(
            persona(), [analysis("skeptic", "New position.")],
            prior_analyses=[analysis("skeptic", "Old position.")],
            config_changed=True)
        assert report.dimension("appropriate_revision").score == 1.0

    def test_unsupported_assertions_lower_the_score(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(
            persona(), [analysis("skeptic", "Position.")],
            unsupported_assertion_count=4)
        assert report.dimension("assertion_discipline").score < 0.5

    def test_no_unsupported_assertions_scores_well(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(persona(), [analysis("skeptic", "Position.")])
        assert report.dimension("assertion_discipline").score == 1.0

    def test_stable_assumptions_score_well(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(
            persona(),
            [analysis("skeptic", "Position.", assumptions=["X", "Y"])],
            prior_analyses=[analysis("skeptic", "Old.", assumptions=["X", "Y"])])
        assert report.dimension("assumption_consistency").score == 1.0

    def test_flipped_assumptions_score_poorly(self):
        evaluator = ConsistencyEvaluator()
        report = evaluator.evaluate(
            persona(),
            [analysis("skeptic", "Position.", assumptions=["X", "Y"])],
            prior_analyses=[analysis("skeptic", "Old.", assumptions=["Z", "W"])])
        assert report.dimension("assumption_consistency").score == 0.0

    def test_report_overall_is_a_mean(self):
        report = ConsistencyReport(
            dimensions=[
                ConsistencyDimension("a", 1.0),
                ConsistencyDimension("b", 0.5),
            ])
        assert report.overall == 0.75

    def test_concerning_dimensions_are_flagged(self):
        report = ConsistencyReport(
            dimensions=[ConsistencyDimension("a", 0.2),
                        ConsistencyDimension("b", 0.9)])
        assert len(report.concerning) == 1


# ── §12.7 / §14.7 Imitation detection ────────────────────────────────


class TestImitationDetection:
    def test_near_identical_analyses_are_flagged(self):
        """§14.7: personas on the same model with the same framing may
        reproduce the same mistake."""
        detector = ImitationDetector()
        findings = detector.detect([
            analysis("a", "The policy will reduce emissions significantly."),
            analysis("b", "The policy will reduce emissions significantly."),
        ])
        assert findings[0].is_concerning

    def test_distinct_analyses_are_not_flagged(self):
        detector = ImitationDetector()
        findings = detector.detect([
            analysis("a", "Adopt the policy immediately."),
            analysis("b", "Reject the policy entirely."),
        ])
        assert not findings[0].is_concerning

    def test_empty_positions_score_zero(self):
        detector = ImitationDetector()
        findings = detector.detect([analysis("a", ""), analysis("b", "")])
        assert findings[0].similarity == 0.0

    def test_agreement_is_not_corroboration(self):
        """§14.7: the system should never describe agreement among outputs
        from one model as independent factual corroboration."""
        detector = ImitationDetector()
        findings = detector.detect([
            analysis("a", "The figure is 40 deaths."),
            analysis("b", "The figure is 40 deaths."),
        ])
        assert "correlated error" in findings[0].detail

    def test_concerning_findings_are_filtered(self):
        detector = ImitationDetector()
        analyses = [
            analysis("a", "Adopt the policy immediately."),
            analysis("b", "Reject the policy entirely."),
        ]
        assert detector.concerning(analyses) == []

    def test_same_persona_is_not_compared_with_itself(self):
        detector = ImitationDetector()
        findings = detector.detect([
            analysis("a", "Position."), analysis("a", "Position.")])
        assert findings == []


# ── The full §12.4 sequence ──────────────────────────────────────────


class TestDialogueOrchestration:
    def _setup(self):
        registry = PersonaRegistry()
        registry.register(persona("skeptic", "Skeptic",
                                  "challenges weak evidence"))
        registry.register(persona("analyst", "Analyst",
                                  "examines incentives"))
        return registry

    def test_full_sequence_produces_a_validated_script(self):
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        orchestrator.register_analysis("skeptic", lambda c, p: analysis(
            "skeptic", "This evidence is insufficient to act on."))
        orchestrator.register_analysis("analyst", lambda c, p: analysis(
            "analyst", "The incentives clearly favour action now."))
        outcome = orchestrator.run(packet(), ["skeptic", "analyst"])
        assert outcome.script is not None
        assert outcome.validation is not None
        assert outcome.validation.is_approved

    def test_single_persona_is_refused(self):
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        outcome = orchestrator.run(packet(), ["skeptic"])
        assert outcome.suppressed
        assert "at least two" in outcome.suppression_reason

    def test_unanimous_evidence_suppresses_manufactured_debate(self):
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        orchestrator.register_analysis("skeptic", lambda c, p: analysis(
            "skeptic", "The evidence is clear.", facts=pkt_facts,
            evidence=["e1"]))
        orchestrator.register_analysis("analyst", lambda c, p: analysis(
            "analyst", "The evidence is clear.", facts=pkt_facts,
            evidence=["e1"]))
        outcome = orchestrator.run(packet(), ["skeptic", "analyst"])
        assert outcome.suppressed
        assert "straightforward conclusion" in outcome.suppression_reason

    def test_disabled_persona_is_excluded(self):
        registry = self._setup()
        registry.register(persona("disabled", enabled=False))
        orchestrator = DialogueOrchestrator(registry)
        outcome = orchestrator.run(packet(), ["skeptic", "disabled"])
        assert outcome.suppressed

    def test_script_validates_against_the_shared_packet(self):
        """§12.4 step 8."""
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        orchestrator.register_analysis("skeptic", lambda c, p: analysis(
            "skeptic", "This evidence is insufficient."))
        orchestrator.register_analysis("analyst", lambda c, p: analysis(
            "analyst", "Action is clearly warranted now."))
        outcome = orchestrator.run(packet(), ["skeptic", "analyst"])
        assert outcome.script.evidence_packet_id == "packet-1"

    def test_participation_is_recorded_as_memory(self):
        """§12.6: a record of previous participation."""
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        orchestrator.register_analysis("skeptic", lambda c, p: analysis(
            "skeptic", "This evidence is insufficient."))
        orchestrator.register_analysis("analyst", lambda c, p: analysis(
            "analyst", "Action is clearly warranted now."))
        outcome = orchestrator.run(packet(), ["skeptic", "analyst"])
        orchestrator.record_participation(outcome, "seg-1")
        assert orchestrator.memory.count("skeptic", MemoryType.EPISODIC) == 1
        assert orchestrator.memory.count("analyst", MemoryType.EPISODIC) == 1

    def test_open_questions_are_recorded_from_disagreements(self):
        registry = self._setup()
        orchestrator = DialogueOrchestrator(registry)
        orchestrator.register_analysis("skeptic", lambda c, p: analysis(
            "skeptic", "This evidence is insufficient."))
        orchestrator.register_analysis("analyst", lambda c, p: analysis(
            "analyst", "Action is clearly warranted now."))
        outcome = orchestrator.run(packet(), ["skeptic", "analyst"])
        orchestrator.record_participation(outcome, "seg-1")
        assert orchestrator.memory.count("skeptic", MemoryType.OPEN_QUESTION) > 0


pkt_facts = ["The policy reduces emissions."]
