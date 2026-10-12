"""The Knowledge Compiler orchestrator.

Spec §8.1: the compiler is a sequence of explicit passes, each with a defined
input, output, version, and failure mode — not a single opaque language-model
operation.

    Source Artifact → Normalized Representation → Extracted Objects
    → Resolved Relationships → Validated Knowledge

This module wires Passes 1–4 and 7–8 together. Pass 5 (entity resolution) and
Pass 6 (event resolution) are implemented *deterministically only*: exact
canonical matches resolve, everything else stays unresolved for review. That
is a deliberate limitation, not an oversight — §5.4 forbids letting similarity
authorize a merge, and clustering several articles into one event needs the
story engine's signals, which arrive in Phase 4.

§8.5: every pass records its version, and a compilation records the versions
of every pass that ran, so "why did a previous compiler version produce a
different result?" is answerable from stored records rather than from memory.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..models import Article, Event, EventStatus
from ..persistence import WorldStore
from .claims import ClaimExtractionResult, ClaimExtractor
from .contradiction import (
    ContradictionAnalyzer, ContradictionReport, ExistingClaimView,
)
from .entities import EntityExtractionResult, EntityExtractor
from .evidence import EvidenceExtractionResult, EvidenceExtractor
from .integration import IntegrationResult, KnowledgeIntegrator
from .structural import NormalizedDocument, StructuralNormalizer, verify_spans


@dataclass
class CompilationResult:
    article_id: str
    document: Optional[NormalizedDocument] = None
    claims: Optional[ClaimExtractionResult] = None
    entities: Optional[EntityExtractionResult] = None
    evidence: Optional[EvidenceExtractionResult] = None
    contradictions: Optional[ContradictionReport] = None
    integration: Optional[IntegrationResult] = None
    status: str = "pending"             # pending | compiled | failed
    error: Optional[str] = None
    span_problems: List[str] = field(default_factory=list)
    pass_versions: Dict[str, str] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def ok(self) -> bool:
        return self.status == "compiled"

    def counts(self) -> Dict[str, int]:
        return {
            "sentences": len(self.document.sentences) if self.document else 0,
            "claims": len(self.claims.claims) if self.claims else 0,
            "evidence": (len(self.evidence.evidence)
                         if self.evidence else 0),
            "entity_mentions": (len(self.entities.mentions)
                                if self.entities else 0),
            "contradictions": (len(self.contradictions.contradictions)
                               if self.contradictions else 0),
        }


class KnowledgeCompiler:
    """Runs the compiler passes in dependency order.

    Each pass is individually substitutable, so a future model-based extractor
    can replace a deterministic one without touching the orchestrator — as
    long as it honours the same input/output contract.
    """

    def __init__(
        self,
        store: WorldStore,
        structural: Optional[StructuralNormalizer] = None,
        entity_extractor: Optional[Any] = None,
        claim_extractor: Optional[ClaimExtractor] = None,
        evidence_extractor: Optional[EvidenceExtractor] = None,
        contradiction_analyzer: Optional[ContradictionAnalyzer] = None,
        schedule_downstream: bool = True,
    ):
        self.store = store
        self.structural = structural or StructuralNormalizer()
        self.entity_extractor = entity_extractor or EntityExtractor()
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_extractor = evidence_extractor or EvidenceExtractor()
        self.contradiction_analyzer = (
            contradiction_analyzer or ContradictionAnalyzer())
        self.integrator = KnowledgeIntegrator(store)
        self.schedule_downstream = schedule_downstream

    async def compile(self, article: Article) -> CompilationResult:
        result = CompilationResult(article_id=article.id)
        result.started_at = datetime.now(timezone.utc)

        # The article must already be durably captured: evidence rows carry an
        # article FK, so compiling an uncaptured article would fail deep inside
        # integration with an opaque SQLite error. Check the precondition here
        # and say what is actually wrong.
        stored = await self.store.get_article(article.id)
        if stored is None:
            result.status = "failed"
            result.error = (
                f"article {article.id} is not captured; ingestion must persist "
                "it before the compiler runs (§8.2 Pass 8 depends on it)")
            result.completed_at = datetime.now(timezone.utc)
            return result
        # Compile against the stored record so timestamps and hashes match
        # what was actually captured.
        article = stored

        # Pass 1 — structural normalization.
        document = self._run_pass(result, 1, self.structural, article)
        if document is None:
            return result

        # Offsets must reproduce the source. A span that does not match cannot
        # support a provenance claim, so this is verified before anything is
        # extracted from it.
        result.span_problems = verify_spans(document)
        if result.span_problems:
            result.status = "failed"
            result.error = (
                "pass 1 produced spans that do not match the source: "
                f"{result.span_problems[:3]}")
            return result

        # Pass 2 — entity extraction (mentions only, never merges).
        entities = self._run_pass(result, 2, self.entity_extractor, document)
        if entities is None:
            return result

        # Pass 3 — claim extraction (attribution preserved).
        claims = self._run_pass(result, 3, self.claim_extractor, document)
        if claims is None:
            return result

        # Pass 4 — evidence extraction (ATTRIBUTES vs SUPPORTS).
        evidence = self._run_pass(result, 4, self.evidence_extractor,
                                  document, claims)
        if evidence is None:
            return result

        # Pass 7 — contradiction and corroboration analysis against what the
        # system already believes.
        existing = await self._existing_claim_views()
        contradictions = self._run_pass(result, 7, self.contradiction_analyzer,
                                        claims.claims, existing)
        if contradictions is None:
            return result

        # Pass 6 — deterministic event resolution.
        events = self._resolve_events(article, document, claims)

        # Pass 8 — knowledge integration, all-or-nothing.
        try:
            integration = self.integrator.integrate(
                article=article,
                document=document,
                claims=claims,
                entities=entities,
                evidence=evidence,
                events=events,
                contradictions=contradictions.findings,
                schedule_downstream=self.schedule_downstream,
            )
        except Exception as exc:
            result.status = "failed"
            result.error = f"pass 8 raised: {exc}"
            return result
        result.integration = integration
        result.pass_versions[integration.pass_name] = integration.pass_version

        result.status = ("failed" if integration.status == "failed"
                         else "compiled")
        result.error = integration.incomplete_reason
        result.completed_at = datetime.now(timezone.utc)
        return result

    # ── pass runner ──────────────────────────────────────

    def _run_pass(self, result: CompilationResult, number: int, pass_object,
                  *args) -> Optional[Any]:
        """Run one pass, recording its output and version, containing failure.

        A failed pass aborts the compilation rather than degrading to partial
        output — §8.2 forbids leaving a partially integrated set of records
        that appears complete.
        """
        try:
            output = pass_object.run(*args)
        except Exception as exc:
            result.status = "failed"
            result.error = f"pass {number} ({pass_object.name}) failed: {exc}"
            return None
        result.pass_versions[pass_object.name] = pass_object.version
        # The result object carries each pass's output so callers can inspect
        # what was extracted without re-running anything.
        _OUTPUT_FIELDS = {
            "structural_normalization": "document",
            "entity_extraction": "entities",
            "claim_extraction": "claims",
            "evidence_extraction": "evidence",
            "contradiction_analysis": "contradictions",
        }
        field_name = _OUTPUT_FIELDS.get(pass_object.name)
        if field_name:
            setattr(result, field_name, output)
        return output

    # ── Pass 5 and 6 helpers ─────────────────────────────

    async def _existing_claim_views(self) -> List[ExistingClaimView]:
        """Stored claims, as the contradiction analyzer sees them."""
        claims = await self.store.list_claims()
        views: List[ExistingClaimView] = []
        for claim in claims:
            metadata = claim.metadata_json or {}
            quantities = [
                (str(q.get("value")), q.get("unit"))
                for q in metadata.get("quantities", [])
                if q.get("value") is not None
            ]
            temporals = [
                t.get("phrase")
                for t in metadata.get("temporal_qualifiers", [])
                if t.get("phrase")
            ]
            views.append(ExistingClaimView(
                claim_id=claim.id,
                canonical_proposition=claim.canonical_proposition,
                claim_type=claim.claim_type.value,
                quantities=quantities,
                temporals=temporals,
                attributed_to=metadata.get("attributed_to"),
                is_causal=bool(metadata.get("is_causal")),
            ))
        return views

    def _resolve_events(self, article: Article,
                        document: NormalizedDocument,
                        claims: ClaimExtractionResult) -> List[Event]:
        """Deterministic event resolution.

        An article whose first sentence is a direct assertion yields one
        candidate occurrence event. Clustering several articles into one event
        needs the story engine's signals and belongs in Phase 4, so this
        deliberately produces at most one conservative candidate.
        """
        if not document.sentences or not claims.claims:
            return []
        first = document.sentences[0].text.strip()
        if not first:
            return []
        return [Event(
            canonical_title=article.title or first[:120],
            event_type="reported_occurrence",
            summary=first,
            status=EventStatus.EMERGING,
            occurred_at_start=article.published_at,
            first_observed_at=article.first_seen_at,
            last_material_update_at=article.first_seen_at,
            confidence_state="unassessed",
            metadata_json={
                "article_id": article.id,
                "basis": "single-article occurrence candidate",
                "resolution_method": "deterministic",
            },
        )]
