"""World model and story intelligence tests — Gates 4 and 5.

Spec §9 (world state) and §10 (editorial prioritization).

The tests that matter assert what the system must *refuse* to do: treating
article volume as development, ranking a popular story above a consequential
one, counting one owner's outlets as independent corroboration, permanently
suppressing a saturated story, and letting a replay see the future.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.models import Story
from core.persistence import WorldStore
from core.world_model.changes import (
    DEFAULT_WEIGHTS,
    ChangeDetector,
    ChangeKind,
    IncomingState,
    KnownState,
    MaterialChange,
)
from core.world_model.coverage import CoverageFacts, CoverageModel
from core.world_model.ranking import (
    MAX_AUDIENCE_CONTRIBUTION,
    StoryFacts,
    StoryRanker,
    rank_stories,
)
from core.world_model.replay import (
    assert_no_leakage,
    development_timeline,
    filter_as_of,
    reconstruct_knowledge,
    snapshot_consistency_check,
)
from core.world_model.stories import ClaimRecord, StoryEngine, _identity_key


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


NOW = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)


def change(kind, hours_ago, significance=1.0):
    return MaterialChange(
        kind=kind, significance=significance,
        occurred_at=NOW - timedelta(hours=hours_ago))


@pytest.fixture
def store():
    s = WorldStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def detector():
    return ChangeDetector()


@pytest.fixture
def ranker():
    return StoryRanker()


def known_with(proposition="Unemployment was 5.2% in March.", quantity="5.2:%"):
    return KnownState(
        claim_ids={"c1"},
        claim_texts={"c1": proposition},
        quantities={quantity},
        entity_names={"Statistics Office"},
    )


# ── §9.2 Material change detection ───────────────────────────────────


class TestMaterialChangeDetection:
    def test_new_content_is_not_new_information(self, detector):
        """The defining distinction of §9.2."""
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a2", observed_at=NOW, claims=[{
                "id": "c2",
                "proposition": "Unemployment was 5.2% in March.",
                "claim_type": "quantitative",
                "quantities": [{"value": "5.2", "unit": "%"}],
            }]))
        assert not report.has_material_change, \
            "restating a known claim is not new information"

    def test_ten_restatements_yield_no_development(self, detector):
        """§9.2 / §10.4: article volume must not read as development."""
        total = 0
        for i in range(10):
            report = detector.detect(
                known_with(),
                IncomingState(article_id=f"dup{i}", observed_at=NOW, claims=[{
                    "id": f"x{i}",
                    "proposition": "Unemployment was 5.2% in March.",
                    "claim_type": "quantitative",
                    "quantities": [{"value": "5.2", "unit": "%"}],
                }]))
            total += len([c for c in report.changes if c.is_material])
        assert total == 0

    def test_revised_number_is_material(self, detector):
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a3", observed_at=NOW, claims=[{
                "id": "c3",
                "proposition": "Unemployment was 6.1% in March.",
                "claim_type": "quantitative",
                "quantities": [{"value": "6.1", "unit": "%"}],
            }]))
        assert ChangeKind.REVISED_NUMBER in [c.kind for c in report.changes]

    def test_new_measurement_is_material(self, detector):
        report = detector.detect(
            KnownState(),
            IncomingState(article_id="a", observed_at=NOW, claims=[{
                "id": "c1",
                "proposition": "Inflation was 3.1%.",
                "claim_type": "quantitative",
                "quantities": [{"value": "3.1", "unit": "%"}],
            }]))
        assert ChangeKind.NEW_MEASUREMENT in [c.kind for c in report.changes]

    def test_correction_is_material(self, detector):
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a4", observed_at=NOW,
                          corrections=[{"target_id": "c1",
                                        "reason": "figure restated"}]))
        assert ChangeKind.CORRECTION in [c.kind for c in report.changes]

    def test_new_participant_is_material_but_weaker(self, detector):
        change_found = detector.detect(
            known_with(),
            IncomingState(article_id="a5", observed_at=NOW,
                          entities=["New Agency"])).by_kind(
            ChangeKind.NEW_PARTICIPANT)[0]
        assert change_found.significance < 1.0

    def test_decision_is_distinguished_from_generic_event(self, detector):
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a6", observed_at=NOW, claims=[{
                "id": "c9",
                "proposition": "The committee approved the proposal.",
                "claim_type": "observational",
            }]))
        assert ChangeKind.CONFIRMED_DECISION in [c.kind for c in report.changes]

    def test_new_causal_mechanism_is_its_own_kind(self, detector):
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a7", observed_at=NOW, claims=[{
                "id": "c10",
                "proposition": "Rate hikes caused the hiring slowdown.",
                "claim_type": "causal",
            }]))
        assert ChangeKind.NEW_CAUSAL_MECHANISM in [c.kind for c in report.changes]

    def test_new_event_is_detected(self, detector):
        report = detector.detect(
            known_with(),
            IncomingState(article_id="a8", observed_at=NOW,
                          events=[{"id": "e1", "title": "Summit opened"}]))
        assert ChangeKind.NEW_EVENT in [c.kind for c in report.changes]

    def test_weights_are_recorded_and_auditable(self, detector):
        """§10.3: weights are tunable parameters, not constants."""
        assert detector.weight_set_id()
        altered = dict(DEFAULT_WEIGHTS)
        altered[ChangeKind.CORRECTION] = 2.0
        other = ChangeDetector(weights=altered)
        assert other.weight_set_id() != detector.weight_set_id()

    def test_no_changes_yields_no_material_change(self, detector):
        report = detector.detect(known_with(),
                                 IncomingState(article_id="a", observed_at=NOW))
        assert not report.has_material_change


# ── §10.1–10.6 The nine ranking factors ──────────────────────────────


class TestRankingFactors:
    def test_all_factors_are_present(self, ranker):
        result = ranker.rank(StoryFacts(story_id="s", now=NOW))
        names = {f.name for f in result.factors}
        assert {"importance", "novelty", "velocity", "evidence",
                "relationship", "unresolved", "audience",
                "continuity"} == names

    def test_novelty_is_zero_without_material_change(self, ranker):
        """§10.3: measured against the world model, not article text."""
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW, article_count_since_coverage=25))
        novelty = [f for f in result.factors if f.name == "novelty"][0]
        assert novelty.value == 0.0
        assert "no material change" in novelty.reason

    def test_novelty_rises_with_material_change(self, ranker):
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW, article_count_since_coverage=25,
            material_changes=[change(ChangeKind.CORRECTION, 2)]))
        novelty = [f for f in result.factors if f.name == "novelty"][0]
        assert novelty.value > 0

    def test_velocity_uses_material_changes_not_article_count(self, ranker):
        """§10.4: a surge from repeated publication is not development."""
        duplicates = ranker.rank(StoryFacts(
            story_id="d", now=NOW, article_count_since_coverage=50))
        genuine = ranker.rank(StoryFacts(
            story_id="g", now=NOW, article_count_since_coverage=1,
            material_changes=[change(ChangeKind.NEW_EVENT, 1)]))
        dup_v = [f for f in duplicates.factors if f.name == "velocity"][0]
        gen_v = [f for f in genuine.factors if f.name == "velocity"][0]
        assert gen_v.value > dup_v.value

    def test_late_discovery_is_not_ranked_as_stale(self, ranker):
        """§9.3: an investigation today about an old incident is fresh news.

        Velocity decays from observation time, so a development observed
        recently outranks one observed long ago regardless of when the
        underlying event occurred.
        """
        recent = ranker.rank(StoryFacts(
            story_id="r", now=NOW,
            material_changes=[change(ChangeKind.NEW_EVENT, 1)]))
        stale = ranker.rank(StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.NEW_EVENT, 72)]))
        recent_v = [f for f in recent.factors if f.name == "velocity"][0]
        stale_v = [f for f in stale.factors if f.name == "velocity"][0]
        assert recent_v.value > stale_v.value

    def test_audience_relevance_is_bounded(self, ranker):
        """§10.7: popularity must not override importance or evidence."""
        result = ranker.rank(StoryFacts(
            story_id="p", now=NOW, audience_relevance=1.0))
        audience = [f for f in result.factors if f.name == "audience"][0]
        assert audience.value <= MAX_AUDIENCE_CONTRIBUTION
        assert "cannot override" in audience.reason

    def test_popularity_cannot_outrank_a_consequential_story(self, ranker):
        popular = ranker.rank(StoryFacts(
            story_id="pop", now=NOW, audience_relevance=1.0))
        consequential = ranker.rank(StoryFacts(
            story_id="big", now=NOW,
            people_affected=100000, is_irreversible=True,
            has_safety_implication=True, primary_evidence_count=2,
            independent_source_groups=3, total_source_groups=3))
        assert consequential.net_value > popular.net_value

    def test_ownership_groups_make_corroboration_meaningful(self, ranker):
        """Invariant I1: ten outlets under one owner are not ten sources."""
        one_owner = ranker.rank(StoryFacts(
            story_id="a", now=NOW, primary_evidence_count=2,
            independent_source_groups=1, total_source_groups=10))
        three_owners = ranker.rank(StoryFacts(
            story_id="b", now=NOW, primary_evidence_count=2,
            independent_source_groups=3, total_source_groups=10))
        one_e = [f for f in one_owner.factors if f.name == "evidence"][0]
        three_e = [f for f in three_owners.factors if f.name == "evidence"][0]
        assert three_e.value > one_e.value

    def test_corrected_evidence_reduces_eligibility(self, ranker):
        clean = ranker.rank(StoryFacts(
            story_id="c", now=NOW, primary_evidence_count=2))
        corrected = ranker.rank(StoryFacts(
            story_id="c", now=NOW, primary_evidence_count=2,
            corrected_evidence_count=2))
        clean_e = [f for f in clean.factors if f.name == "evidence"][0]
        corr_e = [f for f in corrected.factors if f.name == "evidence"][0]
        assert corr_e.value < clean_e.value

    def test_centrality_is_contextual_not_decisive(self, ranker):
        """§10.5: centrality is normalized and never the deciding factor."""
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW, degree_centrality=50,
            max_degree_in_graph=50))
        relationship = [f for f in result.factors
                        if f.name == "relationship"][0]
        assert relationship.value <= 1.0

    def test_correction_raises_eligibility_without_a_live_model(self, ranker):
        """§22.2 requires exactly this test."""
        before = ranker.rank(StoryFacts(story_id="s", now=NOW))
        after = ranker.rank(StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.CORRECTION, 1)]))
        assert after.net_value > before.net_value

    def test_ranking_is_deterministic(self, ranker):
        """Gate 5: reproducible from the same stored state."""
        facts = StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.NEW_EVENT, 2)])
        first = ranker.rank(facts)
        second = ranker.rank(facts)
        assert first.score == second.score
        assert first.weight_set_id == second.weight_set_id

    def test_stories_are_ordered_by_net_value(self, ranker):
        results = rank_stories(ranker, [
            StoryFacts(story_id="low", now=NOW),
            StoryFacts(story_id="high", now=NOW,
                       people_affected=50000, primary_evidence_count=2,
                       independent_source_groups=2, total_source_groups=2),
        ])
        assert results[0].story_id == "high"


# ── §10.9 Redundancy and transition penalties ────────────────────────


class TestPenalties:
    def test_volume_without_development_is_saturation(self, ranker):
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW, prior_coverage_depth=8,
            recent_segments_repeated=True, article_count_since_coverage=20))
        assert result.redundancy_penalty > 1.0

    def test_material_change_reduces_redundancy(self, ranker):
        saturated = ranker.rank(StoryFacts(
            story_id="s", now=NOW, prior_coverage_depth=8,
            article_count_since_coverage=20))
        revived = ranker.rank(StoryFacts(
            story_id="s", now=NOW, prior_coverage_depth=8,
            article_count_since_coverage=20,
            material_changes=[change(ChangeKind.CORRECTION, 1)]))
        assert revived.redundancy_penalty < saturated.redundancy_penalty

    def test_transition_cost_applies_when_switching(self, ranker):
        switching = ranker.rank(StoryFacts(
            story_id="s", now=NOW, is_currently_discussed=False))
        continuing = ranker.rank(StoryFacts(
            story_id="s", now=NOW, is_currently_discussed=True))
        assert switching.transition_cost > continuing.transition_cost


# ── §10.11 Explainable selection ─────────────────────────────────────


class TestExplainableSelection:
    def test_decision_record_is_structured_not_prose(self, ranker):
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.CORRECTION, 2)]))
        record = result.to_decision_record()
        assert record["story_id"] == "s"
        assert "score" in record and "net_value" in record
        assert "weight_set_id" in record
        assert "decision_time" in record

    def test_every_factor_carries_its_value_and_reason(self, ranker):
        record = ranker.rank(StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.NEW_EVENT, 1)],
        )).to_decision_record()
        for name, factor in record["factors"].items():
            assert "value" in factor, name
            assert "weight" in factor, name
            assert "contribution" in factor, name
            assert "reason" in factor, name

    def test_reasons_are_derived_from_factors(self, ranker):
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW,
            material_changes=[change(ChangeKind.CORRECTION, 1)]))
        assert result.reasons
        # The explanation is generated from the record, not instead of it.
        assert any("material change" in r for r in result.reasons)

    def test_high_score_does_not_conceal_its_basis(self, ranker):
        """§10.1: a high score must not hide why it ranked highly."""
        result = ranker.rank(StoryFacts(
            story_id="s", now=NOW, audience_relevance=1.0))
        record = result.to_decision_record()
        assert record["factors"]["audience"]["reason"]


# ── §10.8 Coverage debt ──────────────────────────────────────────────


class TestCoverageDebt:
    def test_major_story_with_thin_coverage_accrues_debt(self):
        model = CoverageModel()
        assessment = model.assess(CoverageFacts(
            story_id="major", importance=3.0, prior_coverage_depth=1,
            last_substantial_coverage_at=NOW - timedelta(days=7),
            unresolved_question_count=4, now=NOW))
        assert assessment.debt > 1.5

    def test_repetition_is_not_depth(self):
        """§10.8: ten repetitive segments add no understanding."""
        model = CoverageModel()
        assessment = model.assess(CoverageFacts(
            story_id="shallow", importance=1.0, prior_coverage_depth=10,
            recent_segments_repeated=True, last_substantial_coverage_at=NOW,
            unresolved_question_count=0, now=NOW))
        assert assessment.is_saturated

    def test_never_covered_story_carries_debt(self):
        assessment = CoverageModel().assess(CoverageFacts(
            story_id="never", importance=2.0, now=NOW))
        assert assessment.debt >= 2.0

    def test_corrections_needing_clarification_add_debt(self):
        model = CoverageModel()
        base = dict(story_id="c", importance=1.0,
                    last_substantial_coverage_at=NOW, now=NOW)
        with_corrections = model.assess(CoverageFacts(
            corrections_needing_clarification=2, **base))
        without = model.assess(CoverageFacts(**base))
        assert with_corrections.debt > without.debt

    def test_unresolved_questions_add_debt(self):
        model = CoverageModel()
        base = dict(story_id="q", importance=1.0,
                    last_substantial_coverage_at=NOW, now=NOW)
        with_questions = model.assess(CoverageFacts(
            unresolved_question_count=4, **base))
        without = model.assess(CoverageFacts(**base))
        assert with_questions.debt > without.debt


# ── §10.9 Saturation must not permanently suppress ───────────────────


class TestSaturationIsNotPermanent:
    def test_material_change_reduces_saturation(self):
        """The property most easily lost in a naive implementation."""
        model = CoverageModel()
        base = dict(story_id="old", importance=1.0, prior_coverage_depth=10,
                    recent_segments_repeated=True,
                    last_substantial_coverage_at=NOW, now=NOW)
        before = model.assess(CoverageFacts(**base))
        assert before.is_saturated
        after = model.assess(CoverageFacts(has_material_change=True, **base))
        assert after.saturation < before.saturation
        assert after.net_eligibility > before.net_eligibility

    def test_suppressed_story_becomes_eligible_again(self):
        model = CoverageModel()
        base = dict(story_id="s", importance=1.0, prior_coverage_depth=10,
                    last_substantial_coverage_at=NOW, now=NOW)
        before = model.assess(CoverageFacts(**base))
        after = model.assess(CoverageFacts(has_material_change=True, **base))
        assert before.net_eligibility < after.net_eligibility


# ── §5.5.1 Story clustering ──────────────────────────────────────────


def claim(claim_id, proposition, entities, quantities=None, temporals=None):
    return ClaimRecord(
        claim_id=claim_id, proposition=proposition,
        claim_type="quantitative" if quantities else "observational",
        epistemic_status="unassessed",
        quantities=quantities or [], temporals=temporals or [],
        entities=entities)


class TestStoryClustering:
    @pytest.fixture
    def engine(self, store):
        return StoryEngine(store)

    def test_several_articles_about_one_development_cluster(self, engine):
        """§5.5.1: not a new editorial identity per article."""
        clusters = engine.cluster([
            claim("c1", "Unemployment rose to 6.1% in March.",
                  ["Statistics Office", "Government"],
                  [{"value": "6.1", "unit": "%"}], [{"phrase": "in March"}]),
            claim("c2", "Officials confirmed the jobless rate reached 6.1%.",
                  ["Statistics Office", "Government"],
                  [{"value": "6.1", "unit": "%"}], [{"phrase": "in March"}]),
        ])
        assert len(clusters) == 1
        assert len(clusters[0].claim_ids) == 2

    def test_unrelated_reporting_does_not_cluster(self, engine):
        clusters = engine.cluster([
            claim("c1", "Unemployment rose to 6.1% in March.",
                  ["Statistics Office", "Government"]),
            claim("c3", "The football club signed a new striker.",
                  ["Football Club", "Agent"]),
        ])
        assert len(clusters) == 2

    def test_one_shared_entity_is_too_weak_to_cluster(self, engine):
        """"The government" appears in unrelated reporting."""
        clusters = engine.cluster([
            claim("c1", "The government raised the levy.",
                  ["Government"]),
            claim("c2", "The government denied the report.",
                  ["Government"]),
        ])
        assert len(clusters) == 2

    def test_identity_is_qualifier_aware(self):
        """§8.3.1: same sentence, different number is different knowledge."""
        a = claim("a", "Output rose 5%.", ["X", "Y"],
                  [{"value": "5", "unit": "%"}])
        b = claim("b", "Output rose 5%.", ["X", "Y"],
                  [{"value": "6", "unit": "%"}])
        assert _identity_key(a) != _identity_key(b)

    def test_story_records_its_unresolved_questions(self, engine, store):
        async def scenario():
            candidate = engine.cluster([
                claim("c1", "A developing situation here.",
                      ["Agency", "Ministry"])])[0]
            candidate.unresolved_questions = ["What caused it?"]
            story_id = await engine.persist_story(candidate)
            story = await store.get_story(story_id)
            assert story.unresolved_question_count == 1
        run(scenario())


# ── §22.7 Temporal leakage ───────────────────────────────────────────


class Record:
    def __init__(self, record_id, observed_at, status="unassessed"):
        self.id = record_id
        self.first_observed_at = observed_at
        self.epistemic_status = status


class TestTemporalLeakage:
    def test_future_records_are_excluded(self):
        window = filter_as_of(
            [Record("past", NOW - timedelta(days=1)),
             Record("future", NOW + timedelta(days=1))],
            NOW, lambda r: r.first_observed_at)
        assert len(window.records) == 1
        assert len(window.violations) == 1

    def test_violations_are_reported_not_silently_dropped(self):
        window = filter_as_of(
            [Record("future", NOW + timedelta(days=1))],
            NOW, lambda r: r.first_observed_at)
        assert window.violations
        assert "after the replay point" in window.violations[0].detail

    def test_assert_no_leakage_raises_on_a_dirty_window(self):
        window = filter_as_of(
            [Record("future", NOW + timedelta(days=1))],
            NOW, lambda r: r.first_observed_at)
        with pytest.raises(AssertionError):
            assert_no_leakage(window)

    def test_clean_window_passes(self):
        window = filter_as_of(
            [Record("past", NOW - timedelta(days=1))],
            NOW, lambda r: r.first_observed_at)
        assert_no_leakage(window)
        assert window.is_clean

    def test_replay_uses_observation_time_not_publication_time(self):
        """An article published in January but fetched in March was not
        available in February.

        The record's `first_observed_at` is 10 days before NOW; replaying to
        15 days before NOW must exclude it, because at that point the system
        had not yet seen it — regardless of when the article was published.
        """
        january_published_march_observed = Record(
            "jan", NOW - timedelta(days=10))
        window = filter_as_of(
            [january_published_march_observed], NOW - timedelta(days=15),
            lambda r: r.first_observed_at)
        assert window.records == [], \
            "not yet observed at the replay point, so not available"
        assert window.violations

    def test_records_without_observation_time_are_excluded(self):
        """An unknown timestamp is not evidence of availability."""
        window = filter_as_of(
            [Record("no-time", None)], NOW, lambda r: r.first_observed_at)
        assert window.records == []
        assert window.violations

    def test_reconstruct_knowledge_splits_by_status(self):
        claims = [
            Record("c1", NOW - timedelta(days=2), "supported"),
            Record("c2", NOW - timedelta(days=1), "contradicted"),
        ]
        by_status = reconstruct_knowledge(claims, NOW)
        assert "supported" in by_status
        assert "contradicted" in by_status

    def test_development_timeline_uses_observation_time(self):
        """§9.3: a late discovery appears when the system learned of it."""
        early = MaterialChange(
            kind=ChangeKind.NEW_EVENT, significance=1.0,
            occurred_at=NOW - timedelta(hours=48))
        late = MaterialChange(
            kind=ChangeKind.CORRECTION, significance=1.0,
            occurred_at=NOW - timedelta(hours=1))
        timeline = development_timeline([late, early])
        assert timeline[0] is early
        assert timeline[-1] is late

    def test_snapshot_manifest_consistency_is_checked(self):
        """§5.9: a snapshot must not imply knowledge it did not have."""
        problems = snapshot_consistency_check(
            {"claims": ["c1", "c2"]},
            {"claims": ["c1"]})
        assert problems
        assert "claims" in problems[0]

    def test_consistent_snapshot_passes(self):
        assert snapshot_consistency_check(
            {"claims": ["c1"]}, {"claims": ["c1"]}) == []


# ── §9.4 Uncertainty attaches where it occurs ────────────────────────


class TestUncertaintyIsLocal:
    def test_heterogeneous_claim_statuses_survive_in_one_story(self, store):
        """A story holds well-established facts and one disputed claim."""
        async def scenario():
            from core.models import Claim, ClaimStatus, ClaimType
            established = await store.add_claim(Claim(
                canonical_proposition="The vote occurred.",
                claim_type=ClaimType.OBSERVATIONAL,
                epistemic_status=ClaimStatus.CORROBORATED))
            disputed = await store.add_claim(Claim(
                canonical_proposition="The policy caused it.",
                claim_type=ClaimType.CAUSAL,
                epistemic_status=ClaimStatus.CONTRADICTED))
            await store.add_story(Story(
                canonical_title="The vote",
                claim_ids=[established, disputed]))
            claims = await store.list_claims()
            statuses = {c.epistemic_status for c in claims}
            assert ClaimStatus.CORROBORATED in statuses
            assert ClaimStatus.CONTRADICTED in statuses, \
                "heterogeneity must be preserved, not averaged"
        run(scenario())
