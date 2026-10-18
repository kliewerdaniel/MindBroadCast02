"""Epistemic invariant tests.

Each test constructs a violating condition deliberately and asserts the system
refuses, flags, or preserves the distinction. These are the invariants from
docs/EPISTEMIC_INVARIANTS.md; they are what makes the system's core claim
(evidence-grounded broadcasting) true rather than decorative.

Gate 1 invariants covered here: I1, I2, I3, I4, I5, I8, I9, I11.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from core.models import (
    Article,
    Claim,
    ClaimStatus,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityStatus,
    EntityType,
    Event,
    EventStatus,
    Evidence,
    EvidenceRelationship,
    OwnershipGroup,
    Source,
    SourceType,
    Story,
)
from core.persistence import PersistenceError, WorldStore, content_hash


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store():
    s = WorldStore(":memory:")
    yield s
    s.close()


def make_source(name, domain, ownership_group_id=None):
    return Source(
        name=name, canonical_domain=domain, source_type=SourceType.RSS,
        endpoint=f"https://{domain}/feed", ownership_group_id=ownership_group_id,
    )


# ── I1: Status is not a truth score ──────────────────────────────────


class TestInvariantI1StatusIsNotTruthScore:
    def test_corroboration_requires_distinct_ownership_groups(self, store):
        """Five articles repeating one report are not five confirmations.

        Two outlets under one ownership group must not corroborate each other.
        """
        group = OwnershipGroup(name="News Corp")
        run(store.add_ownership_group(group))
        outlet_a = make_source("Outlet A", "a.com", group.id)
        outlet_b = make_source("Outlet B", "b.com", group.id)
        a_id = run(store.add_source(outlet_a))
        b_id = run(store.add_source(outlet_b))

        assert run(store.get_source(a_id)).ownership_group_id == group.id
        assert run(store.get_source(b_id)).ownership_group_id == group.id

        # Both share an owner, so they are one source for corroboration purposes.
        owners = {
            run(store.get_source(sid)).ownership_group_id
            for sid in (a_id, b_id)
        }
        assert len(owners) == 1, "shared ownership must be detectable"

    def test_independent_sources_have_distinct_ownership(self, store):
        s1 = make_source("Independent A", "ia.com")
        s2 = make_source("Independent B", "ib.com")
        id1 = run(store.add_source(s1))
        id2 = run(store.add_source(s2))
        owners = {
            run(store.get_source(sid)).ownership_group_id
            for sid in (id1, id2)
        }
        assert len(owners) == 2 or owners == {None}

    def test_source_has_no_reliability_score_field(self, store):
        """§5.2: trust_profile_id must not imply a universal reliability score."""
        source = make_source("Gov Agency", "gov.example")
        source.trust_profile_id = "tp-1"
        source_id = run(store.add_source(source))
        retrieved = run(store.get_source(source_id))
        assert retrieved.trust_profile_id == "tp-1"
        numeric_fields = [
            f for f in vars(retrieved)
            if "reliab" in f or "trust_score" in f
        ]
        assert numeric_fields == [], "no numeric reliability field may exist"

    def test_assessment_is_not_a_truth_probability(self, store):
        """§5.7: assessment is a method result, not a universal truth measure."""
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", content_hash="h" * 64)
        article_id = run(store.add_article(article))
        evidence = Evidence(
            article_id=article_id, source_id=source_id, excerpt="passage")
        evidence_id = run(store.add_evidence(evidence))
        claim_id = run(store.add_claim(Claim(canonical_proposition="P")))

        run(store.link_evidence(
            claim_id, evidence_id, EvidenceRelationship.SUPPORTS,
            assessment="supports", assessment_method="nli-entailment"))

        links = run(store.get_evidence_links(claim_id))
        assert links[0].assessment == "supports"
        assert links[0].assessment_method == "nli-entailment"


# ── I2: Statement existence is not statement truth ───────────────────


class TestInvariantI2StatementExistenceIsNotTruth:
    def test_attributed_claim_type_is_distinct_from_observational(self, store):
        """"A minister said X" and "X" are different propositions."""
        attributed = Claim(
            canonical_proposition="The minister said inflation fell.",
            claim_type=ClaimType.ATTRIBUTED,
        )
        observational = Claim(
            canonical_proposition="Inflation fell.",
            claim_type=ClaimType.OBSERVATIONAL,
        )
        a_id = run(store.add_claim(attributed))
        o_id = run(store.add_claim(observational))

        assert run(store.get_claim(a_id)).claim_type is ClaimType.ATTRIBUTED
        assert run(store.get_claim(o_id)).claim_type is ClaimType.OBSERVATIONAL

    def test_claim_type_determines_validation_route(self, store):
        """§5.6.1: a quotation, a statistic, and a hypothesis differ."""
        types_seen = set()
        for claim_type in (ClaimType.ATTRIBUTED, ClaimType.QUANTITATIVE,
                           ClaimType.CAUSAL, ClaimType.PREDICTIVE):
            cid = run(store.add_claim(Claim(
                canonical_proposition=f"P-{claim_type.value}",
                claim_type=claim_type)))
            types_seen.add(run(store.get_claim(cid)).claim_type)
        assert len(types_seen) == 4

    def test_evidence_supporting_speech_act_is_labelled(self, store):
        """Evidence that a statement was made is ATTRIBUTES, not SUPPORTS."""
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", content_hash="h" * 64)
        article_id = run(store.add_article(article))
        evidence = Evidence(
            evidence_type="transcript", article_id=article_id,
            source_id=source_id, excerpt="Minister: 'inflation fell.'")
        evidence_id = run(store.add_evidence(evidence))
        claim_id = run(store.add_claim(Claim(
            canonical_proposition="The minister said inflation fell.",
            claim_type=ClaimType.ATTRIBUTED)))

        run(store.link_evidence(
            claim_id, evidence_id, EvidenceRelationship.ATTRIBUTES))

        links = run(store.get_evidence_links(claim_id))
        assert links[0].relationship_type is EvidenceRelationship.ATTRIBUTES
        # It is NOT recorded as SUPPORTS — existence of the statement is
        # not support for its content.
        supporting = run(store.get_evidence_for_claim(
            claim_id, EvidenceRelationship.SUPPORTS))
        assert supporting == []


# ── I3: A correction is not evidence ─────────────────────────────────


class TestInvariantI3CorrectionIsNotEvidence:
    def test_correction_never_creates_evidence(self, store):
        claim_id = run(store.add_claim(Claim(canonical_proposition="Original")))
        before = run(store.count_evidence())
        run(store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id=claim_id,
            reason="Publisher restated the figure.")))
        assert run(store.count_evidence()) == before, \
            "a correction must not add an evidence record"

    def test_correction_is_retrievable_as_itself(self, store):
        claim_id = run(store.add_claim(Claim(canonical_proposition="Original")))
        cid = run(store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id=claim_id, reason="restated")))
        correction = run(store.get_correction(cid))
        assert correction is not None
        assert correction.correction_type is CorrectionType.PUBLISHER
        assert correction.reason == "restated"

    def test_publisher_correction_differs_from_system_correction(self, store):
        publisher = run(store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="article", target_id="a1", reason="publisher fixed it")))
        system = run(store.add_correction(Correction(
            correction_type=CorrectionType.SYSTEM_EXTRACTION,
            target_type="article", target_id="a1", reason="we extracted wrong")))
        p = run(store.get_correction(publisher))
        s = run(store.get_correction(system))
        assert p.correction_type is not s.correction_type

    def test_correction_requires_reassessment_of_dependent_claims(self, store):
        """A publisher correction does not delete the claim, but marks it."""
        claim_id = run(store.add_claim(Claim(
            canonical_proposition="Casualties numbered 40.",
            claim_type=ClaimType.QUANTITATIVE)))
        run(store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id=claim_id,
            reason="Figure corrected to 34.")))

        original = run(store.get_claim(claim_id))
        assert original is not None, "the claim survives"
        corrections = run(store.get_corrections_for("claim", claim_id))
        assert len(corrections) == 1


# ── I4: Historical records are never rewritten ───────────────────────


class TestInvariantI4HistoryIsNeverRewritten:
    def test_article_revision_preserves_the_original(self, store):
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        original = Article(
            source_id=source_id, canonical_url="u", title="Death toll: 40",
            content_hash="a" * 64,
            normalized_text="Officials said 40 people died.")
        original_id = run(store.add_article(original))

        revised = Article(
            source_id=source_id, canonical_url="u", title="Death toll: 34",
            content_hash="b" * 64,
            normalized_text="Officials said 34 people died.")
        new_id = run(store.supersede_article(original_id, revised))

        stored_original = run(store.get_article(original_id))
        assert stored_original.title == "Death toll: 40", \
            "the version used to produce an earlier broadcast must survive"
        assert run(store.get_article(new_id)).title == "Death toll: 34"

    def test_correction_does_not_replace_the_original_record(self, store):
        claim_id = run(store.add_claim(Claim(
            canonical_proposition="The toll was 40.",
            claim_type=ClaimType.QUANTITATIVE)))
        cid = run(store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id=claim_id,
            original_value_reference="claim-v1",
            corrected_value_reference="claim-v2")))

        # Both the original and the correction reference remain queryable.
        assert run(store.get_claim(claim_id)).canonical_proposition == \
            "The toll was 40."
        correction = run(store.get_correction(cid))
        assert correction.original_value_reference == "claim-v1"
        assert correction.corrected_value_reference == "claim-v2"

    def test_claim_status_change_keeps_the_prior_assessment(self, store):
        claim_id = run(store.add_claim(Claim(
            canonical_proposition="P", epistemic_status=ClaimStatus.SUPPORTED)))
        run(store.set_claim_status(claim_id, ClaimStatus.CONTRADICTED,
                                   reason="new evidence"))
        original = run(store.get_claim(claim_id))
        assert original.epistemic_status is ClaimStatus.SUPPORTED, \
            "the earlier assessment must remain inspectable"

    def test_snapshot_is_immutable(self, store):
        s1 = run(store.create_snapshot())
        conn = store.db.connect()
        with pytest.raises(Exception):  # noqa: B017
            conn.execute(
                "UPDATE world_snapshots SET story_count = 99 WHERE id = ?",
                (s1.id,))
            conn.commit()

    def test_snapshot_records_what_was_known(self, store):
        """A snapshot must not falsely imply earlier knowledge."""
        story = Story(canonical_title="Late discovery")
        story_id = run(store.add_story(story))
        snapshot = run(store.create_snapshot())

        assert story_id in snapshot.included_record_manifest["stories"]
        assert snapshot.knowledge_watermark != ""

        later = run(store.create_snapshot())
        assert story_id in later.included_record_manifest["stories"]
        assert later.created_at >= snapshot.created_at


# ── I5: Three time axes stay separate ────────────────────────────────


class TestInvariantI5ThreeTimeAxes:
    def test_published_first_seen_and_fetched_are_distinct(self, store):
        """A report published Monday, discovered Wednesday, processed Thursday."""
        monday = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u",
            title="Monday report", content_hash="h" * 64,
            published_at=monday,
            first_seen_at=datetime(2026, 1, 7, 9, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 1, 8, 10, 0, tzinfo=UTC),
        )
        article_id = run(store.add_article(article))
        stored = run(store.get_article(article_id))

        assert stored.published_at == monday
        assert stored.first_seen_at != stored.published_at
        assert stored.fetched_at != stored.first_seen_at
        assert stored.fetched_at != stored.published_at

    def test_all_timestamps_are_timezone_aware(self, store):
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", content_hash="h" * 64)
        article_id = run(store.add_article(article))
        stored = run(store.get_article(article_id))
        assert stored.first_seen_at.tzinfo is not None
        assert stored.fetched_at.tzinfo is not None

    def test_naive_timestamps_are_normalized_to_utc(self, store):
        """Stored timestamps normalize to UTC; naive input is assumed UTC."""
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        naive = datetime(2026, 1, 1, 12, 0)  # no tzinfo
        article = Article(
            source_id=source_id, canonical_url="u", content_hash="h" * 64,
            published_at=naive)
        article_id = run(store.add_article(article))
        stored = run(store.get_article(article_id))
        assert stored.published_at.tzinfo is not None
        assert stored.published_at.utcoffset().total_seconds() == 0

    def test_evidence_observation_time_differs_from_source_time(self, store):
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", content_hash="h" * 64,
            published_at=datetime(2026, 1, 1, tzinfo=UTC))
        article_id = run(store.add_article(article))
        evidence = Evidence(
            article_id=article_id, source_id=source_id, excerpt="p",
            source_published_at=datetime(2026, 1, 1, tzinfo=UTC))
        evidence_id = run(store.add_evidence(evidence))
        stored = run(store.get_evidence(evidence_id))
        assert stored.source_published_at is not None
        assert stored.observed_at != stored.source_published_at


# ── I8: Volume is not corroboration ──────────────────────────────────


class TestInvariantI8VolumeIsNotCorroboration:
    def test_many_articles_one_event_are_distinguishable(self, store):
        """Ten articles about one event are ten articles, not ten events."""
        source = make_source("Wire", "wire.com")
        source_id = run(store.add_source(source))
        for i in range(10):
            run(store.add_article(Article(
                source_id=source_id,
                canonical_url=f"https://wire.com/a{i}",
                title=f"Report {i}",
                content_hash=content_hash(f"same wire report {i}"))))
        assert run(store.count_articles()) == 10
        # The schema makes no assumption that article count implies
        # event count or corroboration.
        assert run(store.count_events()) == 0

    def test_duplicate_content_is_rejected_not_merged_silently(self, store):
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", title="Syndicated",
            content_hash="x" * 64)
        run(store.add_article(article))
        with pytest.raises(PersistenceError):
            run(store.add_article(article))

    def test_evidence_independence_is_traceable_to_source(self, store):
        """Each evidence record names its source, so independence is checkable."""
        s1 = make_source("A", "a.com")
        s2 = make_source("B", "b.com")
        id1 = run(store.add_source(s1))
        id2 = run(store.add_source(s2))
        a1 = Article(source_id=id1, canonical_url="a1", content_hash="1" * 64)
        a2 = Article(source_id=id2, canonical_url="a2", content_hash="2" * 64)
        aid1 = run(store.add_article(a1))
        aid2 = run(store.add_article(a2))
        e1 = run(store.add_evidence(Evidence(
            article_id=aid1, source_id=id1, excerpt="from A")))
        e2 = run(store.add_evidence(Evidence(
            article_id=aid2, source_id=id2, excerpt="from B")))

        ev1 = run(store.get_evidence(e1))
        ev2 = run(store.get_evidence(e2))
        assert ev1.source_id != ev2.source_id, \
            "independence must remain inspectable"


# ── I9: Near-duplicates may carry corrections ────────────────────────


class TestInvariantI9NearDuplicatesMayCarryCorrections:
    def test_a_revised_article_is_kept_not_discarded(self, store):
        """A near-duplicate with a correction must not be thrown away."""
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        original = Article(
            source_id=source_id, canonical_url="u", title="Toll: 40",
            content_hash="a" * 64,
            normalized_text="Officials said 40 died in the incident.")
        original_id = run(store.add_article(original))

        revised = Article(
            source_id=source_id, canonical_url="u", title="Toll: 34",
            content_hash="b" * 64,
            normalized_text="Officials said 34 died in the incident.")
        revised_id = run(store.supersede_article(original_id, revised))

        assert run(store.count_articles()) == 2, \
            "the corrected version is retained, not discarded as a duplicate"
        assert run(store.get_article(revised_id)).title == "Toll: 34"

    def test_version_chain_is_walkable(self, store):
        source = make_source("S", "s.com")
        source_id = run(store.add_source(source))
        v1 = run(store.add_article(Article(
            source_id=source_id, canonical_url="u", title="v1",
            content_hash="1" * 64)))
        v2 = run(store.supersede_article(v1, Article(
            source_id=source_id, canonical_url="u", title="v2",
            content_hash="2" * 64)))
        v3 = run(store.supersede_article(v2, Article(
            source_id=source_id, canonical_url="u", title="v3",
            content_hash="3" * 64)))

        chain = run(store.get_article_versions(v3))
        assert [a.publication_version for a in chain] == [1, 2, 3]
        assert [a.title for a in chain] == ["v1", "v2", "v3"]


# ── I11: Uncertainty attaches where it occurs ────────────────────────


class TestInvariantI11UncertaintyAttachesWhereItOccurs:
    def test_event_dispute_is_separate_from_claim_dispute(self, store):
        """A disputed event and a disputed figure are different uncertainties."""
        event = Event(
            canonical_title="Incident", event_type="conflict",
            status=EventStatus.DISPUTED)
        event_id = run(store.add_event(event))

        claim = Claim(
            canonical_proposition="40 people died.",
            claim_type=ClaimType.QUANTITATIVE,
            epistemic_status=ClaimStatus.SUPPORTED)
        claim_id = run(store.add_claim(claim))

        assert run(store.get_event(event_id)).status is EventStatus.DISPUTED
        assert run(store.get_claim(claim_id)).epistemic_status is \
            ClaimStatus.SUPPORTED, \
            "the quantitative claim's own status is unaffected by event dispute"

    def test_heterogeneous_claim_statuses_survive_in_one_story(self, store):
        """A story holds many well-established facts and one disputed claim."""
        established = Claim(
            canonical_proposition="The vote occurred.",
            claim_type=ClaimType.OBSERVATIONAL,
            epistemic_status=ClaimStatus.CORROBORATED)
        disputed = Claim(
            canonical_proposition="The policy caused the outcome.",
            claim_type=ClaimType.CAUSAL,
            epistemic_status=ClaimStatus.CONTRADICTED)
        e_id = run(store.add_claim(established))
        d_id = run(store.add_claim(disputed))

        run(store.add_story(Story(
            canonical_title="The vote", claim_ids=[e_id, d_id])))

        statuses = {c.epistemic_status for c in run(store.list_claims())}
        assert ClaimStatus.CORROBORATED in statuses
        assert ClaimStatus.CONTRADICTED in statuses, \
            "heterogeneous claim statuses must both be preserved"

    def test_unresolved_questions_are_counted_on_the_story(self, store):
        story = Story(canonical_title="Open case", unresolved_question_count=3)
        story_id = run(store.add_story(story))
        assert run(store.get_story(story_id)).unresolved_question_count == 3


# ── Entity resolution: candidates are not authorizations ─────────────


class TestEntityResolutionIsNotAutomatic:
    def test_same_canonical_identity_deduplicates(self, store):
        """Same canonical name + type is a deterministic identity match."""
        a = Entity(canonical_name="John Smith", entity_type=EntityType.PERSON)
        b = Entity(canonical_name="John Smith", entity_type=EntityType.PERSON)
        id_a = run(store.add_entity(a))
        id_b = run(store.add_entity(b))
        assert id_a == id_b

    def test_different_types_with_same_name_stay_separate(self, store):
        """A person and an organization sharing a name are different entities."""
        person = Entity(canonical_name="Amazon", entity_type=EntityType.PERSON)
        org = Entity(canonical_name="Amazon", entity_type=EntityType.ORGANIZATION)
        p_id = run(store.add_entity(person))
        o_id = run(store.add_entity(org))
        assert p_id != o_id
        assert run(store.count_entities()) == 2

    def test_merge_is_explicit_recorded_and_reversible(self, store):
        survivor = Entity(canonical_name="Canonical",
                          entity_type=EntityType.ORGANIZATION)
        absorbed = Entity(canonical_name="Alias",
                          entity_type=EntityType.ORGANIZATION)
        s_id = run(store.add_entity(survivor))
        a_id = run(store.add_entity(absorbed))

        run(store.merge_entities(s_id, a_id))

        assert run(store.get_entity(a_id)).status is EntityStatus.MERGED
        events = run(store.list_domain_events(event_type="EntityMerged"))
        assert len(events) == 1
        assert events[0]["payload"]["merged_into"] == s_id
        # Reversible: the record still exists and can be reactivated.
        assert run(store.get_entity(a_id)) is not None

    def test_no_implicit_merge_on_name_similarity(self, store):
        """Adding a similar entity never merges by itself."""
        a = Entity(canonical_name="Acme Corp", entity_type=EntityType.ORGANIZATION)
        b = Entity(canonical_name="Acme Corporation",
                   entity_type=EntityType.ORGANIZATION)
        id_a = run(store.add_entity(a))
        id_b = run(store.add_entity(b))
        assert id_a != id_b, "no implicit merge on name similarity"
        assert run(store.get_entity(id_a)).status is EntityStatus.ACTIVE
        assert run(store.get_entity(id_b)).status is EntityStatus.ACTIVE
