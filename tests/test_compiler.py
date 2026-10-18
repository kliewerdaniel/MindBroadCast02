"""Knowledge Compiler tests — Gate 3.

Spec §8. Claims and evidence must have traceable provenance.

The tests that matter most are the ones asserting what the compiler must
*refuse* to do: laundering an attributed statement into a factual assertion,
recording a transcript as supporting the proposition it only reports, and
calling two different months a contradiction.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from core.compiler.cku import CKUAssembler, canonical_identity
from core.compiler.claims import ClaimExtractor
from core.compiler.contradiction import (
    AnalysisOutcome,
    ContradictionAnalyzer,
    ExistingClaimView,
)
from core.compiler.entities import EntityExtractor, RuleBasedEntityExtractor
from core.compiler.evidence import EvidenceExtractor
from core.compiler.orchestrator import KnowledgeCompiler
from core.compiler.structural import (
    StructuralNormalizer,
    verify_spans,
)
from core.models import (
    Article,
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceRelationship,
    ExtractionStatus,
    Source,
)
from core.persistence import WorldStore


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def document_of(text, title=""):
    return StructuralNormalizer().run(
        Article(id="art-1", title=title, normalized_text=text))


def compile_text(text):
    doc = document_of(text)
    claims = ClaimExtractor().run(doc)
    evidence = EvidenceExtractor().run(doc, claims)
    return doc, claims, evidence


@pytest.fixture
def store():
    s = WorldStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def captured(store):
    """A durably captured article, which is the compiler's precondition."""
    async def make(text, title="Test"):
        source_id = await store.add_source(Source(
            name="Test Wire", endpoint="https://w.example/feed"))
        source = await store.get_source(source_id)
        article = Article(
            source_id=source.id,
            canonical_url=f"https://w.example/{title.lower().replace(' ', '-')}",
            title=title,
            content_hash=f"{abs(hash(text)):064x}"[:64],
            normalized_text=text,
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
            extraction_status=ExtractionStatus.PENDING,
        )
        await store.add_article(article)
        return await store.get_article(article.id)
    return make


# ── Pass 1: structural normalization ─────────────────────────────────


class TestStructuralNormalization:
    def test_sentences_are_segmented(self):
        doc = document_of("First sentence here. Second sentence follows.")
        assert len(doc.sentences) == 2

    def test_offsets_reproduce_the_source(self):
        """§8.2: enough location information to identify the original passage."""
        text = ("The committee approved the proposal on Tuesday. "
                "Officials said the measure takes effect in March.")
        doc = document_of(text)
        for sentence in doc.sentences:
            assert text[sentence.start:sentence.end] == sentence.text

    def test_offsets_are_monotonic_and_non_overlapping(self):
        text = "One. Two. Three. Four."
        doc = document_of(text)
        previous_end = -1
        for sentence in doc.sentences:
            assert sentence.start > previous_end
            previous_end = sentence.end

    def test_abbreviation_is_not_a_sentence_boundary(self):
        doc = document_of(
            "Dr. Smith said costs had risen. The report was published.")
        assert len(doc.sentences) == 2
        assert doc.sentences[0].text.startswith("Dr. Smith")

    def test_paragraphs_are_segmented_with_offsets(self):
        text = "First paragraph.\n\nSecond paragraph."
        doc = document_of(text)
        assert len(doc.paragraphs) == 2
        for paragraph in doc.paragraphs:
            assert text[paragraph.start:paragraph.end] == paragraph.text

    def test_quotations_are_located(self):
        doc = document_of('"We expect support," the chairperson said today.')
        assert len(doc.quotations) == 1

    def test_trailing_attribution_names_the_speaker(self):
        doc = document_of('"We expect support," the chairperson said.')
        assert doc.quotations[0].speaker == "chairperson"

    def test_speaker_is_scoped_to_the_sentence(self):
        """A quote must not be attributed to whoever spoke last."""
        doc = document_of(
            "The minister spoke first. \"A different view,\" an analyst said.")
        if doc.quotations:
            assert doc.quotations[0].speaker != "minister"

    def test_empty_content_is_flagged_not_fatal(self):
        doc = document_of("")
        assert doc.has_content is False
        assert any(w.code == "empty_content" for w in doc.warnings)

    def test_pass_is_versioned(self):
        assert StructuralNormalizer.version
        doc = document_of("A sentence.")
        assert doc.pass_version == StructuralNormalizer.version

    def test_verify_spans_detects_a_bad_offset(self):
        doc = document_of("A sentence here.")
        doc.sentences[0].start = 999
        assert verify_spans(doc)

    def test_verify_spans_passes_for_a_good_document(self):
        doc = document_of("A sentence here. And another one.")
        assert verify_spans(doc) == []

    def test_sentence_at_offset(self):
        text = "First one. Second one."
        doc = document_of(text)
        sentence = doc.sentence_at(text.index("Second"))
        assert sentence.text == "Second one."


# ── Pass 2: entity extraction ────────────────────────────────────────


class TestEntityExtraction:
    def test_mentions_carry_exact_offsets(self):
        text = ("The European Central Bank raised rates. "
                "Christine Lagarde spoke afterwards.")
        doc = document_of(text)
        result = EntityExtractor().run(doc)
        assert result.mentions
        for mention in result.mentions:
            assert text[mention.start:mention.end] == mention.text

    def test_extraction_method_and_version_are_recorded(self):
        doc = document_of("The European Central Bank raised rates on Thursday.")
        result = EntityExtractor().run(doc)
        for mention in result.mentions:
            assert mention.extraction_method
            assert mention.extraction_version

    def test_dates_are_not_entity_candidates(self):
        """A temporal qualifier maps to no EntityType, so it never becomes an
        entity record. Temporal information belongs to the claim's qualifiers,
        not to the entity table."""
        doc = document_of("Rates rose in March according to the bank.")
        result = EntityExtractor().run(doc)
        dates = [m for m in result.mentions if m.raw_label == "DATE"]
        assert dates, "the date is still detected as a mention"
        assert all(m.entity_type is None for m in dates), \
            "a DATE mention must not map to an EntityType"

    def test_no_merge_occurs_as_a_side_effect(self):
        """§8.2 Pass 2: must not merge into a canonical entity."""
        doc = document_of("The European Central Bank spoke. "
                          "The European Central Bank acted.")
        result = EntityExtractor().run(doc)
        # Two mentions, still unresolved — resolution is Pass 5's decision.
        names = [m.text for m in result.mentions]
        assert names.count("The European Central Bank") == 2

    def test_rule_based_fallback_has_exact_offsets(self):
        text = "Christine Lagarde spoke. The bank responded quickly."
        doc = document_of(text)
        result = RuleBasedEntityExtractor().run(doc)
        for mention in result.mentions:
            assert text[mention.start:mention.end] == mention.text

    def test_empty_document_yields_no_mentions(self):
        result = EntityExtractor().run(document_of(""))
        assert result.mentions == []
        assert result.warnings


# ── Pass 3: claim extraction (the attribution requirement) ───────────


class TestClaimExtractionAttribution:
    """§8.2 / invariant I2 — the single most consequential requirement."""

    def test_attributed_statement_is_not_observational(self):
        _, claims, _ = compile_text(
            "A minister said that the policy had reduced inflation.")
        assert claims.claims[0].claim_type is ClaimType.ATTRIBUTED

    def test_direct_assertion_is_observational(self):
        _, claims, _ = compile_text("The policy reduced inflation.")
        assert claims.claims[0].claim_type is ClaimType.OBSERVATIONAL

    def test_hedged_attributed_statement_stays_attributed(self):
        _, claims, _ = compile_text(
            "An analyst argued that the policy may reduce inflation.")
        assert claims.claims[0].claim_type is ClaimType.ATTRIBUTED
        assert claims.claims[0].attributed_to is not None

    def test_the_three_spec_sentences_differ(self):
        """The three §8.2 examples must not collapse into one type."""
        texts = [
            "A minister said that the policy had reduced inflation.",
            "The policy reduced inflation.",
            "An analyst argued that the policy may reduce inflation.",
        ]
        results = []
        for text in texts:
            _, claims, _ = compile_text(text)
            results.append(claims.claims[0])
        assert results[0].claim_type is ClaimType.ATTRIBUTED
        assert results[1].claim_type is ClaimType.OBSERVATIONAL
        assert results[2].claim_type is ClaimType.ATTRIBUTED
        assert results[0].attributed_to != results[1].attributed_to

    def test_attribution_survives_extraction(self):
        _, claims, _ = compile_text(
            "Officials said the death toll had reached forty people.")
        claim = claims.claims[0]
        assert claim.claim_type is ClaimType.ATTRIBUTED
        assert claim.attributed_to is not None
        assert claim.reporting_verb is not None

    def test_reporting_verb_is_recorded(self):
        _, claims, _ = compile_text("The bank announced that rates would hold.")
        assert claims.claims[0].reporting_verb is not None


class TestClaimExtractionTypes:
    @pytest.mark.parametrize("text,expected", [
        ("Unemployment rose to 5.2% in March.", ClaimType.QUANTITATIVE),
        ("The storm caused widespread flooding across the region.",
         ClaimType.CAUSAL),
        ("Analysts expect growth to slow next quarter.", ClaimType.PREDICTIVE),
        ("The government should raise the minimum wage immediately.",
         ClaimType.NORMATIVE),
        ("The policy reduced inflation.", ClaimType.OBSERVATIONAL),
    ])
    def test_claim_type_classification(self, text, expected):
        _, claims, _ = compile_text(text)
        assert claims.claims[0].claim_type is expected

    def test_inflected_hedge_is_still_a_prediction(self):
        """"expects", "expected" — a missed inflection would silently turn a
        prediction into an observational claim."""
        for text in ("The bank expects growth to slow.",
                     "The bank expected growth to slow.",
                     "The bank expect growth to slow."):
            _, claims, _ = compile_text(text)
            assert claims.claims[0].claim_type is ClaimType.PREDICTIVE, text

    def test_quantities_carry_units(self):
        _, claims, _ = compile_text("Unemployment rose to 5.2% in March.")
        assert claims.claims[0].quantities
        assert claims.claims[0].quantities[0].unit == "%"

    def test_temporal_qualifiers_are_extracted(self):
        """Pass 7 cannot distinguish a contradiction from a definitional
        difference without these."""
        _, claims, _ = compile_text(
            "Unemployment rose to 5.2% in March, up from 4.8% in January.")
        assert len(claims.claims[0].temporal_qualifiers) == 2

    def test_quantity_offsets_are_exact(self):
        text = "Unemployment rose to 5.2% in March."
        _, claims, _ = compile_text(text)
        quantity = claims.claims[0].quantities[0]
        assert "5.2" in text[quantity.start:quantity.end]

    def test_claim_offsets_are_exact(self):
        text = "The committee approved the proposal on Tuesday."
        _, claims, _ = compile_text(text)
        assert text[claims.claims[0].start:claims.claims[0].end] == \
            claims.claims[0].source_text

    def test_extraction_confidence_is_not_evidentiary_support(self):
        """§8.2: these are different quantities and must stay distinct."""
        _, claims, _ = compile_text("Officials said the figure was final.")
        claim = claims.claims[0]
        assert 0.0 < claim.confidence < 1.0
        # No field conflates the two.
        assert not hasattr(claim, "support")

    def test_questions_are_not_propositions(self):
        _, claims, _ = compile_text("Will the committee approve the proposal?")
        assert claims.claims == []

    def test_bare_quotation_is_not_a_proposition(self):
        _, claims, _ = compile_text('"A view held by some people here."')
        assert claims.claims == []


# ── Pass 4: evidence extraction ──────────────────────────────────────


class TestEvidenceExtraction:
    def test_direct_assertion_is_supports(self):
        doc, claims, evidence = compile_text("The policy reduced inflation.")
        assert evidence.evidence[0].relationship is \
            EvidenceRelationship.SUPPORTS

    def test_attributed_statement_is_attributes_not_supports(self):
        """§8.2: a transcript evidences that a statement was made, not that
        its content is true."""
        doc, claims, evidence = compile_text(
            "The minister said the policy had reduced inflation.")
        link = evidence.evidence[0]
        assert link.relationship is EvidenceRelationship.ATTRIBUTES

        supporting = [e for e in evidence.evidence
                      if e.relationship is EvidenceRelationship.SUPPORTS]
        assert supporting == [], \
            "an attributed statement must never be recorded as SUPPORTS"

    def test_rationale_is_recorded(self):
        doc, claims, evidence = compile_text("The minister said rates would hold.")
        assert evidence.evidence[0].rationale

    def test_reference_points_at_the_source_location(self):
        text = "The policy reduced inflation."
        doc, claims, evidence = compile_text(text)
        reference = evidence.evidence[0].source_span_reference
        assert reference.startswith("art-1:")
        start, end = _span_from_reference(reference)
        assert text[start:end] == evidence.evidence[0].excerpt

    def test_evidence_is_traceable_to_its_passage(self):
        """A generated summary without a traceable passage is not evidence."""
        doc, claims, evidence = compile_text("The policy reduced inflation.")
        for item in evidence.evidence:
            assert item.source_span_reference
            assert item.excerpt

    def test_no_claims_means_no_evidence(self):
        evidence = EvidenceExtractor().run(document_of(""), _empty_claims())
        assert evidence.evidence == []


def _span_from_reference(reference):
    body = reference.split(":")[1]
    start, end = body.split("-")
    return int(start), int(end)


def _empty_claims():
    from core.compiler.claims import ClaimExtractionResult
    return ClaimExtractionResult(article_id="art-1")


# ── Pass 7: contradiction analysis ───────────────────────────────────


class TestContradictionAnalysis:
    def _existing(self, proposition, value, unit, temporal):
        return [ExistingClaimView(
            claim_id="existing-1",
            canonical_proposition=proposition,
            claim_type="quantitative",
            quantities=[(value, unit)],
            temporals=[temporal],
        )]

    def test_same_scope_different_value_is_a_contradiction(self):
        _, claims, _ = compile_text("Unemployment rose to 6.1% in March.")
        existing = self._existing(
            "Unemployment rose to 5.2% in March.", "5.2", "%", "in March")
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.contradictions, \
            "same unit and scope with different values must contradict"

    def test_different_temporal_scope_is_not_a_contradiction(self):
        """§8.2 / invariant I10: two months are not a conflict."""
        _, claims, _ = compile_text("Unemployment rose to 6.1% in April.")
        existing = self._existing(
            "Unemployment was 5.2% in March.", "5.2", "%", "in March")
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.contradictions == [], \
            "differing temporal scope must not be flagged as a contradiction"
        assert any(f.outcome is AnalysisOutcome.TEMPORAL_DIFFERENCE
                   for f in report.findings)

    def test_same_value_is_corroboration(self):
        _, claims, _ = compile_text("Unemployment was 5.2% in March.")
        existing = self._existing(
            "Unemployment stood at 5.2% in March.", "5.2", "%", "in March")
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.corroborations

    def test_unrelated_reporting_yields_no_findings(self):
        _, claims, _ = compile_text("Unemployment was 5.2% in March.")
        existing = [ExistingClaimView(
            claim_id="c9",
            canonical_proposition="The football club signed a new striker.",
            quantities=[("3", "million")],
        )]
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.findings == []

    def test_speaker_disagreement_is_not_a_contradiction(self):
        _, claims, _ = compile_text(
            "The minister said inflation had fallen sharply.")
        existing = [ExistingClaimView(
            claim_id="c2",
            canonical_proposition="The opposition leader said inflation fell.",
            attributed_to="opposition leader",
        )]
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.contradictions == []

    def test_finding_records_its_basis(self):
        """Every finding must explain itself, not just assert a verdict."""
        _, claims, _ = compile_text("Unemployment rose to 6.1% in March.")
        existing = self._existing(
            "Unemployment rose to 5.2% in March.", "5.2", "%", "in March")
        report = ContradictionAnalyzer().run(claims.claims, existing)
        assert report.contradictions[0].basis

    def test_pass_is_versioned(self):
        report = ContradictionAnalyzer().run([], [])
        assert report.pass_version


# ── Pass 8: integration ──────────────────────────────────────────────


class TestKnowledgeIntegration:
    def test_claims_are_committed(self, store, captured):
        async def scenario():
            article = await captured(
                "The policy reduced inflation. Officials confirmed the change.")
            result = await KnowledgeCompiler(store).compile(article)
            assert result.ok, result.error
            assert await store.count_claims() >= 2
        run(scenario())

    def test_every_claim_traces_to_its_source_passage(self, store, captured):
        """Gate 3: the defining requirement."""
        async def scenario():
            text = "The committee approved the proposal on Tuesday."
            article = await captured(text)
            result = await KnowledgeCompiler(store).compile(article)
            assert result.ok, result.error
            claims = await store.list_claims()
            for claim in claims:
                span = claim.metadata_json["span"]
                assert text[span[0]:span[1]] == claim.canonical_proposition
        run(scenario())

    def test_evidence_is_linked_to_its_claim(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            result = await KnowledgeCompiler(store).compile(article)
            assert result.ok, result.error
            assert await store.count_evidence_links() >= 1
        run(scenario())

    def test_extractor_version_is_recorded(self, store, captured):
        """§8.5: which extractor produced this claim?"""
        async def scenario():
            article = await captured("The policy reduced inflation.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            for claim in claims:
                assert claim.created_by_component
                assert "@" in claim.created_by_component
        run(scenario())

    def test_compilation_records_all_pass_versions(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            result = await KnowledgeCompiler(store).compile(article)
            assert len(result.pass_versions) >= 5
        run(scenario())

    def test_downstream_work_is_scheduled(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            result = await KnowledgeCompiler(store).compile(article)
            assert result.ok
            assert result.integration.outbox_message_id is not None
        run(scenario())

    def test_uncaptured_article_fails_clearly(self, store):
        """The precondition is stated, not surfaced as a raw SQLite error."""
        async def scenario():
            article = Article(
                source_id="ghost", canonical_url="u", content_hash="h" * 64,
                normalized_text="Some text here.")
            result = await KnowledgeCompiler(store).compile(article)
            assert result.status == "failed"
            assert "not captured" in result.error
        run(scenario())

    def test_integration_does_not_mutate_the_captured_article(self, store,
                                                              captured):
        """`articles` is append-only; a compilation must not UPDATE it."""
        async def scenario():
            article = await captured("The policy reduced inflation.")
            before = await store.get_article(article.id)
            await KnowledgeCompiler(store).compile(article)
            after = await store.get_article(article.id)
            assert after.title == before.title
            assert after.content_hash == before.content_hash
            assert after.canonical_url == before.canonical_url
        run(scenario())

    def test_domain_events_are_emitted(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            await KnowledgeCompiler(store).compile(article)
            events = await store.list_domain_events(
                event_type="KnowledgeCompiled")
            assert len(events) == 1
        run(scenario())

    def test_entities_are_recorded(self, store, captured):
        async def scenario():
            article = await captured(
                "The European Central Bank raised rates on Thursday.")
            await KnowledgeCompiler(store).compile(article)
            assert await store.count_entities() >= 1
        run(scenario())

    def test_claims_start_unassessed(self, store, captured):
        """Extraction is not assessment: a new claim is UNASSESSED, never
        SUPPORTED on the strength of being extracted."""
        async def scenario():
            article = await captured("The policy reduced inflation.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            assert all(c.epistemic_status is ClaimStatus.UNASSESSED
                       for c in claims)
        run(scenario())


# ── CKU (§8.3) ───────────────────────────────────────────────────────


class TestCanonicalKnowledgeUnits:
    def test_cku_is_assembled_from_existing_objects(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            cku = await CKUAssembler(store).for_claim(claims[0])
            assert cku.claim_id == claims[0].id
            assert cku.unit_type == "claim_backed"
            assert cku.evidence_references
        run(scenario())

    def test_cku_records_extractor_not_a_second_store(self, store, captured):
        async def scenario():
            article = await captured("The policy reduced inflation.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            cku = await CKUAssembler(store).for_claim(claims[0])
            assert cku.provenance_manifest["created_by_component"]
        run(scenario())

    def test_cku_separates_extraction_confidence_from_support(self, store,
                                                              captured):
        async def scenario():
            article = await captured("Officials said the figure was final.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            cku = await CKUAssembler(store).for_claim(claims[0])
            assert "extraction_confidence" in cku.provenance_manifest
        run(scenario())

    def test_identity_does_not_depend_on_text(self):
        """§8.3.1: same assertion, different wording -> same identity."""
        a = Claim(canonical_proposition="Output rose 5%.",
                  claim_type=ClaimType.QUANTITATIVE)
        b = Claim(canonical_proposition="Production increased by five percent.",
                  claim_type=ClaimType.QUANTITATIVE)
        quantities = [{"value": "5", "unit": "%"}]
        assert canonical_identity(a, quantities) == \
            canonical_identity(b, quantities)

    def test_identity_distinguishes_different_quantities(self):
        """Same sentence, different number -> different knowledge."""
        claim = Claim(canonical_proposition="Output rose.",
                      claim_type=ClaimType.QUANTITATIVE)
        assert canonical_identity(claim, [{"value": "5", "unit": "%"}]) != \
            canonical_identity(claim, [{"value": "6", "unit": "%"}])

    def test_identity_distinguishes_different_temporal_scope(self):
        claim = Claim(canonical_proposition="Output rose.",
                      claim_type=ClaimType.QUANTITATIVE)
        march = [{"phrase": "in March"}]
        april = [{"phrase": "in April"}]
        assert canonical_identity(claim, [], march) != \
            canonical_identity(claim, [], april)

    def test_attributing_references_are_separate_from_supporting(self, store,
                                                                 captured):
        """The relationship distinction must survive into the CKU."""
        async def scenario():
            article = await captured("The minister said inflation had fallen.")
            await KnowledgeCompiler(store).compile(article)
            claims = await store.list_claims()
            cku = await CKUAssembler(store).for_claim(claims[0])
            assert cku.attributing_references
            assert not cku.supporting_references, \
                "an attributed statement is not support for its content"
        run(scenario())


# ── Ingestion does not interpret (§7.1) ──────────────────────────────


class TestCompilerScope:
    def test_no_stories_are_created(self, store, captured):
        """Story creation is Phase 4."""
        async def scenario():
            article = await captured(
                "The committee approved the proposal on Tuesday.")
            await KnowledgeCompiler(store).compile(article)
            assert await store.count_stories() == 0
        run(scenario())

    def test_compilation_is_deterministic(self, store, captured):
        """The same input produces the same extraction, which is what makes
        the pass versioning of §8.5 meaningful."""
        async def scenario():
            text = "The policy reduced inflation by two percent."
            article = await captured(text)
            await KnowledgeCompiler(store).compile(article)
            doc = document_of(text)
            claims = ClaimExtractor().run(doc)
            second = ClaimExtractor().run(document_of(text))
            assert [c.claim_type for c in claims.claims] == \
                [c.claim_type for c in second.claims]
        run(scenario())
