"""Domain model for MindBroadcast.

Canonical data model per docs/DOMAIN_MODEL.md (spec §5).

Core distinctions this module enforces:

- A Source is a first-class entity, not a URL string.
- An Article is a captured publication artifact. An Event is a modeled
  real-world occurrence. A Story is an editorial grouping. These are three
  different things and must never be conflated (spec §5.3, §5.5, §5.5.1).
- A Correction records that something requires revision. It is NOT evidence
  (spec §5.8).
- A Claim carries both a type (§5.6.1) and an epistemic status (§5.6.2).
  Type determines which validation procedures apply.
- ClaimEvidence is an association: one passage may support one claim while
  contradicting another (spec §5.7).
- All timestamps are timezone-aware and stored in UTC. Valid time, observation
  time, and transaction time are distinct concepts (§5.9.1).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
import uuid


def _utcnow() -> datetime:
    """Timezone-aware UTC now. Never a naive datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Enumerations ─────────────────────────────────────────────────────


class SourceType(Enum):
    RSS = "rss"
    ATOM = "atom"
    API = "api"
    OFFICIAL_DOCUMENT = "official_document"
    WEB_PAGE = "web_page"
    OTHER = "other"


class EntityType(Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    CONCEPT = "concept"
    COUNTRY = "country"
    INSTITUTION = "institution"
    PRODUCT = "product"
    LAW = "law"
    CONFLICT = "conflict"


class EntityStatus(Enum):
    ACTIVE = "active"
    MERGED = "merged"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class ClaimType(Enum):
    """Determines which validation procedures are appropriate (§5.6.1).

    A directly attributable quotation, a statistical estimate, and a causal
    hypothesis must not be assessed by the same verification rule.
    """
    OBSERVATIONAL = "observational"
    ATTRIBUTED = "attributed"
    QUANTITATIVE = "quantitative"
    CAUSAL = "causal"
    PREDICTIVE = "predictive"
    NORMATIVE = "normative"
    INTERPRETIVE = "interpretive"


class ClaimStatus(Enum):
    """Epistemic status, distinct from claim type (§5.6.2).

    Not a universal numerical measure of truth. CORROBORATED requires
    genuinely distinct supporting evidence, not repeated publication of the
    same original assertion.
    """
    UNASSESSED = "unassessed"
    SUPPORTED = "supported"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


# Legacy member names from the pre-Phase-1 model, mapped onto the spec status
# set so historical data and older call sites keep working. These are aliases
# of real members, not separate statuses.
ClaimStatus.UNVERIFIED = ClaimStatus.UNASSESSED
ClaimStatus.PARTIALLY_VERIFIED = ClaimStatus.SUPPORTED
ClaimStatus.VERIFIED = ClaimStatus.CORROBORATED

_LEGACY_STATUS_NAMES = {
    "unverified": ClaimStatus.UNASSESSED,
    "partially_verified": ClaimStatus.SUPPORTED,
    "verified": ClaimStatus.CORROBORATED,
}


def claim_status_from_str(value: str) -> ClaimStatus:
    """Resolve a stored status string, tolerating legacy names."""
    try:
        return ClaimStatus(value)
    except ValueError:
        member = _LEGACY_STATUS_NAMES.get(value)
        if member is not None:
            return member
        raise


class EvidenceRelationship(Enum):
    """How evidence relates to a claim (§5.7).

    One document can support one proposition while contradicting another,
    so this lives on the association, not on the evidence record.
    """
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    ATTRIBUTES = "attributes"
    PROVIDES_CONTEXT = "provides_context"
    LEAVES_UNRESOLVED = "leaves_unresolved"


class EventStatus(Enum):
    """The system's representation of an event, not an objective state (§5.5).

    An event can be concluded by one source and ongoing by another. Such
    disagreements are represented, not silently resolved.
    """
    EMERGING = "emerging"
    ACTIVE = "active"
    DEVELOPING = "developing"
    DORMANT = "dormant"
    CONCLUDED = "concluded"
    DISPUTED = "disputed"
    MERGED = "merged"
    SUPERSEDED = "superseded"


class StoryStatus(Enum):
    ACTIVE = "active"
    UPDATING = "updating"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class CorrectionType(Enum):
    """Publisher correction vs. system extraction correction are different (§5.8)."""
    PUBLISHER = "publisher"
    SYSTEM_EXTRACTION = "system_extraction"
    ENTITY_RESOLUTION = "entity_resolution"
    EVENT_ASSOCIATION = "event_association"
    CLAIM_ASSESSMENT = "claim_assessment"
    BROADCAST = "broadcast"
    ADMINISTRATIVE = "administrative"


class EpistemicCategory(Enum):
    FACT = "fact"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    ANALYSIS = "analysis"
    INTERPRETATION = "interpretation"
    SPECULATION = "speculation"
    FICTION = "fiction"
    SATIRE = "satire"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class ExtractionStatus(Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class OperatingMode(Enum):
    """System-level operating mode, distinct from editorial mode (§2.3).

    The system can be LIVE while performing a historical-analysis segment.
    Health and programming decisions are reasoned about independently.
    """
    LIVE = "live"
    IDLE = "idle"
    DEGRADED = "degraded"
    REPLAY = "replay"
    SIMULATION = "simulation"
    MAINTENANCE = "maintenance"
    STOPPING = "stopping"
    STOPPED = "stopped"


# ── Source and ownership ─────────────────────────────────────────────


@dataclass
class OwnershipGroup:
    """A parent organization controlling multiple publications (§5.2).

    Ten publications controlled by one organization are not ten independent
    confirmations. Without this, CORROBORATED is meaningless.
    """
    id: str = field(default_factory=_new_id)
    name: str = ""
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class Source:
    """An identifiable origin of information (§5.2).

    A source is NOT an article. trust_profile_id must not imply a single
    universal reliability score — evaluation is claim- and context-sensitive.
    """
    id: str = field(default_factory=_new_id)
    name: str = ""
    canonical_domain: Optional[str] = None
    source_type: SourceType = SourceType.RSS
    endpoint: str = ""
    publisher_identity: Optional[str] = None
    ownership_group_id: Optional[str] = None
    trust_profile_id: Optional[str] = None
    enabled: bool = True
    poll_interval_seconds: int = 300
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    failure_count: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


# ── Article ──────────────────────────────────────────────────────────


@dataclass
class Article:
    """A captured publication or document (§5.3).

    A source artifact, not a verified account of reality.

    first_seen_at and published_at are deliberately separate: a story
    published three hours ago but discovered now is not equivalent to one
    first observed three hours ago.
    """
    id: str = field(default_factory=_new_id)
    source_id: str = ""
    canonical_url: str = ""
    original_url: str = ""
    title: str = ""
    author: Optional[str] = None
    published_at: Optional[datetime] = None       # valid time
    source_updated_at: Optional[datetime] = None
    first_seen_at: datetime = field(default_factory=_utcnow)   # observation time
    fetched_at: datetime = field(default_factory=_utcnow)      # transaction time
    language: str = "en"
    content_hash: str = ""
    normalized_text: str = ""
    raw_content_reference: Optional[str] = None
    extraction_status: ExtractionStatus = ExtractionStatus.PENDING
    publication_version: int = 1
    supersedes_article_id: Optional[str] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Entity ───────────────────────────────────────────────────────────


@dataclass
class Entity:
    """A recognized real-world or conceptual subject (§5.4)."""
    id: str = field(default_factory=_new_id)
    canonical_name: str = ""
    entity_type: EntityType = EntityType.CONCEPT
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    external_identifiers: Dict[str, str] = field(default_factory=dict)
    canonical_uri: Optional[str] = None
    first_observed_at: datetime = field(default_factory=_utcnow)
    last_observed_at: datetime = field(default_factory=_utcnow)
    status: EntityStatus = EntityStatus.ACTIVE
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Event ────────────────────────────────────────────────────────────


@dataclass
class Event:
    """A real-world occurrence or developing situation (§5.5).

    NOT an article cluster. Multiple articles can discuss one event; one
    article can discuss several; a continuing story contains a series of
    related events without all being identical.
    """
    id: str = field(default_factory=_new_id)
    canonical_title: str = ""
    event_type: str = ""
    summary: str = ""
    status: EventStatus = EventStatus.EMERGING
    occurred_at_start: Optional[datetime] = None
    occurred_at_end: Optional[datetime] = None
    first_observed_at: datetime = field(default_factory=_utcnow)
    last_material_update_at: datetime = field(default_factory=_utcnow)
    parent_event_id: Optional[str] = None
    supersedes_event_id: Optional[str] = None
    confidence_state: str = "unassessed"
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Story ────────────────────────────────────────────────────────────


@dataclass
class Story:
    """An editorial grouping tracking a subject over time (§5.5.1).

    An event is a modeled occurrence; a story may encompass multiple related
    events, articles, claims, and questions.
    """
    id: str = field(default_factory=_new_id)
    canonical_title: str = ""
    editorial_summary: str = ""
    status: StoryStatus = StoryStatus.ACTIVE
    created_at: datetime = field(default_factory=_utcnow)
    last_material_update_at: datetime = field(default_factory=_utcnow)
    last_broadcast_at: Optional[datetime] = None
    last_substantial_coverage_at: Optional[datetime] = None
    audience_relevance_state: str = "neutral"
    unresolved_question_count: int = 0
    entity_ids: List[str] = field(default_factory=list)
    claim_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    corrected_by: Optional[str] = None


# ── Claim and evidence ───────────────────────────────────────────────


@dataclass
class Claim:
    """A proposition that can be evaluated against evidence (§5.6)."""
    id: str = field(default_factory=_new_id)
    canonical_proposition: str = ""
    claim_type: ClaimType = ClaimType.OBSERVATIONAL
    epistemic_status: ClaimStatus = ClaimStatus.UNASSESSED
    normalized_subject_id: Optional[str] = None
    normalized_predicate: Optional[str] = None
    normalized_object: Optional[str] = None
    first_observed_at: datetime = field(default_factory=_utcnow)
    last_evaluated_at: Optional[datetime] = None
    valid_time_start: Optional[datetime] = None
    valid_time_end: Optional[datetime] = None
    supersedes_claim_id: Optional[str] = None
    created_by_component: str = ""
    confidence: float = 0.0
    corrected_by: Optional[str] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence:
    """A specific evidentiary observation relevant to a claim (§5.7).

    Not synonymous with an article. One article can provide several distinct
    pieces of evidence. A generated summary without a traceable underlying
    passage is not primary evidence.
    """
    id: str = field(default_factory=_new_id)
    evidence_type: str = "passage"
    article_id: Optional[str] = None
    source_id: str = ""
    content_reference: str = ""
    excerpt: str = ""
    content_hash: str = ""
    observed_at: datetime = field(default_factory=_utcnow)
    source_published_at: Optional[datetime] = None
    extraction_method: str = ""
    extraction_model: Optional[str] = None
    extraction_version: str = ""
    provenance_status: str = "unverified"
    metadata_json: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimEvidence:
    """Evidence-to-claim association (§5.7).

    relationship_type lives here, not on Evidence: one passage can support
    claim A while contradicting claim B.

    assessment may hold a calibrated score or categorical result, but neither
    is a universal truth probability without validation.
    """
    id: str = field(default_factory=_new_id)
    claim_id: str = ""
    evidence_id: str = ""
    relationship_type: EvidenceRelationship = EvidenceRelationship.SUPPORTS
    assessment: Optional[str] = None
    assessment_method: str = ""
    assessed_at: Optional[datetime] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Correction ───────────────────────────────────────────────────────


@dataclass
class Correction:
    """A record that a prior representation requires revision (§5.8).

    First-class domain object. Must NOT be represented as an Evidence record.
    Evidence establishes or challenges propositions; a correction records that
    something needs revision.

    A publisher correction does not prove every associated claim false, but it
    requires reassessment of claims that depended on the corrected material.
    """
    id: str = field(default_factory=_new_id)
    correction_type: CorrectionType = CorrectionType.PUBLISHER
    target_type: str = ""          # "article" | "claim" | "event" | "entity" | "broadcast_segment"
    target_id: str = ""
    originating_source_id: Optional[str] = None
    original_value_reference: str = ""
    corrected_value_reference: str = ""
    reason: str = ""
    detected_at: datetime = field(default_factory=_utcnow)
    effective_at: Optional[datetime] = None
    review_status: str = "pending"
    supersedes_correction_id: Optional[str] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Snapshots ────────────────────────────────────────────────────────


@dataclass
class WorldSnapshot:
    """The system's best recorded view of a portion of the information
    environment at a point in time (§5.9).

    NOT an omniscient snapshot of the real world — a versioned representation
    of what the system had observed, processed, and retained.
    """
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utcnow)
    knowledge_watermark: str = ""
    schema_version: int = 1
    snapshot_type: str = "full"
    parent_snapshot_id: Optional[str] = None
    content_hash: str = ""
    included_record_manifest: Dict[str, List[str]] = field(default_factory=dict)
    unresolved_issue_count: int = 0
    story_count: int = 0
    claim_count: int = 0
    evidence_count: int = 0
    entity_count: int = 0
    article_count: int = 0
    state_reference: Optional[str] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)


# ── Filters ──────────────────────────────────────────────────────────


@dataclass
class StoryFilter:
    status: Optional[StoryStatus] = None
    entity_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None


@dataclass
class ClaimFilter:
    status: Optional[ClaimStatus] = None
    claim_type: Optional[ClaimType] = None
    source_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None


@dataclass
class SnapshotDiff:
    a_id: str = ""
    b_id: str = ""
    new_stories: List[str] = field(default_factory=list)
    new_claims: List[str] = field(default_factory=list)
    new_evidence: List[str] = field(default_factory=list)
    new_entities: List[str] = field(default_factory=list)
    new_articles: List[str] = field(default_factory=list)
    new_corrections: List[str] = field(default_factory=list)
    changed_claims: List[str] = field(default_factory=list)
    human_readable: str = ""
