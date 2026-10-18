"""Listener message processing.

Spec §17.1–17.3.

The governing rule is §17.1: an incoming message "should not synchronously
block article ingestion, knowledge compilation, or audio delivery." It becomes a
durable input event that the editorial planner evaluates. So this module never
touches the broadcast or the world model directly — it enqueues.

§17.3's ten-step pipeline, and the two refusals inside it:

- Step 7: "A listener question should not automatically be answered on air."
  Eligibility is a decision, not a default.
- §17.5: "A listener request may create a research task. It must not directly
  establish a claim in the world model." Enforced structurally: the pipeline's
  only outputs are a queued task and a durable event. There is no write path
  to claims, evidence, or stories.
"""

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple


class MessageType(Enum):
    QUESTION = "question"
    TOPIC_REQUEST = "topic_request"
    CLARIFICATION = "clarification"
    FEEDBACK = "feedback"
    OTHER = "other"


class ModerationStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    FLAGGED = "flagged"           # held for review
    REJECTED = "rejected"


class ProcessingStatus(Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    DEFERRED = "deferred"
    DECLINED = "declined"
    RESPONDED = "responded"
    SUMMARIZED = "summarized"
    COMBINED = "combined"


@dataclass
class ListenerMessage:
    """§17.2. Persistent listener identity is optional (§17.2)."""
    id: str
    session_id: str
    client_id: str
    received_at: datetime
    content: str
    message_type: MessageType = MessageType.OTHER
    topic_candidates: List[str] = field(default_factory=list)
    moderation_status: ModerationStatus = ModerationStatus.PENDING
    processing_status: ProcessingStatus = ProcessingStatus.RECEIVED
    response_segment_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.strip().lower().encode()
                              ).hexdigest()[:16]


@dataclass
class ResearchTask:
    """§17.5: a listener request may create a research task.

    A task is a proposal for the planner to evaluate. It carries no epistemic
    standing — it is not evidence and cannot become a claim.
    """
    id: str
    message_id: str
    question: str
    topic_candidates: List[str] = field(default_factory=list)
    requires_review: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""
    retry_after_seconds: int = 0


class RateLimiter:
    """§17.5: per-client rate limits and restrictions on how rapidly one
    client can influence editorial priorities."""

    def __init__(
        self,
        max_messages_per_minute: int = 5,
        max_messages_per_hour: int = 30,
        max_queue_influence: int = 3,        # distinct topics per client per hour
        window_minutes: int = 1,
        window_hours: int = 1,
    ):
        self.max_per_minute = max_messages_per_minute
        self.max_per_hour = max_messages_per_hour
        self.max_queue_influence = max_queue_influence
        self.window_minutes = window_minutes
        self.window_hours = window_hours
        self._minute: Dict[str, Deque[datetime]] = defaultdict(deque)
        self._hour: Dict[str, Deque[datetime]] = defaultdict(deque)
        self._topics: Dict[str, Dict[str, datetime]] = defaultdict(dict)

    def check(self, client_id: str, topic: str = "",
              now: Optional[datetime] = None) -> RateLimitDecision:
        now = now or datetime.now(timezone.utc)
        self._expire(client_id, now)

        minute_window = self._minute[client_id]
        if len(minute_window) >= self.max_per_minute:
            oldest = minute_window[0]
            retry = int((oldest + timedelta(minutes=self.window_minutes)
                         - now).total_seconds())
            return RateLimitDecision(
                False, "too many messages this minute", max(retry, 1))

        hour_window = self._hour[client_id]
        if len(hour_window) >= self.max_per_hour:
            oldest = hour_window[0]
            retry = int((oldest + timedelta(hours=self.window_hours)
                         - now).total_seconds())
            return RateLimitDecision(
                False, "too many messages this hour", max(retry, 1))

        # §17.5: bounds on how rapidly one client can shift editorial focus.
        if topic:
            client_topics = self._topics[client_id]
            if topic not in client_topics and len(client_topics) >= (
                    self.max_queue_influence):
                return RateLimitDecision(
                    False,
                    "client has reached its topic-influence quota", 0)

        return RateLimitDecision(True)

    def record(self, client_id: str, topic: str = "",
               now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._minute[client_id].append(now)
        self._hour[client_id].append(now)
        if topic:
            self._topics[client_id][topic] = now

    def _expire(self, client_id: str, now: datetime) -> None:
        minute_cutoff = now - timedelta(minutes=self.window_minutes)
        hour_cutoff = now - timedelta(hours=self.window_hours)
        self._minute[client_id] = deque(
            t for t in self._minute[client_id] if t > minute_cutoff)
        self._hour[client_id] = deque(
            t for t in self._hour[client_id] if t > hour_cutoff)
        self._topics[client_id] = {
            t: ts for t, ts in self._topics[client_id].items()
            if ts > hour_cutoff}


# ── §17.5 Abuse classification ───────────────────────────────────────

SPAM_MARKERS = (
    "buy now", "click here", "free money", "subscribe", "visit my",
    "check out my", "http://", "https://", "www.", "crypto", "investment opportunity",
)

# §17.5: attempts to induce the system to repeat false claims.
INDUCEMENT_MARKERS = (
    "say that", "report that", "state that", "claim that", "tell everyone",
    "announce that", "confirm that", "broadcast that",
)

# Stems, matched with startswith, so inflections ("embezzle"/"embezzled",
# "conspire"/"conspired") are all caught.
SERIOUS_ALLEGATION_MARKERS = (
    "murder", "steal", "stole", "embezzl", "cover up", "covered up",
    "fabricat", "lied", "conspir", "traitor", "criminal", "bribe",
)


@dataclass
class AbuseAssessment:
    is_spam: bool = False
    is_duplicate: bool = False
    is_coordinated: bool = False
    induces_false_claim: bool = False
    involves_serious_allegation: bool = False
    requires_review: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def should_reject(self) -> bool:
        return self.is_spam or self.induces_false_claim

    @property
    def moderation(self) -> ModerationStatus:
        if self.should_reject:
            return ModerationStatus.REJECTED
        if self.requires_review or self.involves_serious_allegation:
            return ModerationStatus.FLAGGED
        return ModerationStatus.APPROVED


class AbuseClassifier:
    """§17.5. Spam, coordinated manipulation, false-claim induction.

    Pattern-based — a floor, not a solution. It catches the obvious cases;
    sophisticated coordination needs analysis this does not do. Recorded as a
    limitation.
    """

    def __init__(self, max_message_length: int = 1000):
        self.max_message_length = max_message_length

    @staticmethod
    def _matches_stem(lowered: str, markers) -> bool:
        """Match a marker as a word stem, so inflections are caught.

        "embezzl" matches "embezzle" and "embezzled"; "conspir" matches
        "conspire" and "conspired". A fixed-phrase list silently misses
        rephrasing, which is the failure §17.5 cannot afford.
        """
        words = lowered.replace(",", " ").replace(".", " ").split()
        for marker in markers:
            if " " in marker:
                if marker in lowered:
                    return True
                continue
            for word in words:
                if word.startswith(marker):
                    return True
        return False

    def classify(
        self,
        message: ListenerMessage,
        recent_hashes: Optional[List[str]] = None,
        client_message_count_recent: int = 0,
        coordinated_client_count: int = 0,
    ) -> AbuseAssessment:
        assessment = AbuseAssessment()
        lowered = message.content.lower()

        if len(message.content) > self.max_message_length:
            assessment.reasons.append("exceeds maximum length")

        if any(marker in lowered for marker in SPAM_MARKERS):
            assessment.is_spam = True
            assessment.reasons.append("spam markers present")

        if any(marker in lowered for marker in INDUCEMENT_MARKERS):
            assessment.induces_false_claim = True
            assessment.reasons.append(
                "attempts to induce an unsupported claim")

        if self._matches_stem(lowered, SERIOUS_ALLEGATION_MARKERS):
            assessment.involves_serious_allegation = True
            assessment.reasons.append("involves a serious allegation")

        # §17.5 duplicate detection.
        if recent_hashes and message.content_hash in recent_hashes:
            assessment.is_duplicate = True
            assessment.reasons.append("duplicate of a recent message")

        # §17.5 coordinated manipulation: the same content from many clients
        # in a short window.
        if coordinated_client_count >= 5:
            assessment.is_coordinated = True
            assessment.requires_review = True
            assessment.reasons.append(
                f"same content from {coordinated_client_count} clients")

        if assessment.involves_serious_allegation or assessment.is_coordinated:
            assessment.requires_review = True

        return assessment


# ── §17.4 Feedback signals ───────────────────────────────────────────


class FeedbackSignal(Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    TOPIC_REQUEST = "topic_request"
    CLARIFICATION_REQUEST = "clarification_request"
    SKIP = "skip"
    REPLAY = "replay"
    LISTENING_DURATION = "listening_duration"
    RATING = "rating"


@dataclass
class FeedbackRecord:
    signal: FeedbackSignal
    segment_id: str = ""
    value: Optional[float] = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FeedbackCollector:
    """§17.4. Signals are noisy evidence about audience preferences.

    The docstring is the contract: these "should not be interpreted as direct
    measures of truth, importance, or public consensus. A replay may indicate
    confusion rather than interest. A skipped segment may reflect timing or
    technical problems rather than dislike of its content."

    So `interpretation()` returns a confidence-capped preference signal, never
    a truth or importance score.
    """

    AMBIGUOUS_SIGNALS = {
        FeedbackSignal.REPLAY: "may indicate confusion rather than interest",
        FeedbackSignal.SKIP: ("may reflect timing or technical problems rather "
                              "than dislike"),
        FeedbackSignal.LISTENING_DURATION: ("affected by connection quality "
                                            "and app behaviour"),
    }

    def __init__(self):
        self._records: List[FeedbackRecord] = []

    def record(self, signal: FeedbackSignal, segment_id: str = "",
               value: Optional[float] = None) -> FeedbackRecord:
        entry = FeedbackRecord(signal=signal, segment_id=segment_id, value=value)
        self._records.append(entry)
        return entry

    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for entry in self._records:
            key = entry.signal.value
            result[key] = result.get(key, 0) + 1
        return result

    def ambiguity_notes(self) -> List[str]:
        """The caveats that must accompany any use of these signals."""
        present = {r.signal for r in self._records}
        return [self.AMBIGUOUS_SIGNALS[s] for s in self.AMBIGUOUS_SIGNALS
                if s in present]

    def preference_signal(self, segment_id: str) -> Dict[str, Any]:
        """A bounded, explicitly noisy preference reading.

        Never a truth or importance score. The confidence cap is the point:
        §17.4 forbids treating these as direct measures.
        """
        relevant = [r for r in self._records if r.segment_id == segment_id]
        if not relevant:
            return {"segment_id": segment_id, "signal": 0.0,
                    "confidence": 0.0, "noisy": True}
        positive = sum(1 for r in relevant
                       if r.signal is FeedbackSignal.POSITIVE)
        negative = sum(1 for r in relevant
                       if r.signal is FeedbackSignal.NEGATIVE)
        total = positive + negative
        raw = (positive - negative) / total if total else 0.0
        return {
            "segment_id": segment_id,
            "signal": round(raw, 4),
            "confidence": round(min(total / 10.0, 0.5), 4),   # capped at 0.5
            "noisy": True,
            "caveats": self.ambiguity_notes(),
        }


# ── §17.1/§17.3 The processing pipeline ──────────────────────────────


class MessagePipeline:
    """The §17.3 ten-step pipeline. Enqueues; never writes to the world model.

    The absence of any claims/evidence/stories write path is the structural
    enforcement of §17.5.
    """

    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
        classifier: Optional[AbuseClassifier] = None,
        event_store: Optional[Any] = None,
        queue_quota: int = 20,          # max queued tasks (§17.5 queue quotas)
    ):
        self.rate_limiter = rate_limiter or RateLimiter()
        self.classifier = classifier or AbuseClassifier()
        self.event_store = event_store
        self.queue_quota = queue_quota
        self.messages: Dict[str, ListenerMessage] = {}
        self.tasks: Deque[ResearchTask] = deque()
        self.recent_hashes: Deque[str] = deque(maxlen=200)

    def submit(self, session_id: str, client_id: str, content: str,
               now: Optional[datetime] = None) -> Tuple[ListenerMessage, str]:
        """Run steps 1–8. Returns the message and a disposition.

        This method does not block: it enqueues a durable event and a task.
        It never touches the broadcast or the world model (§17.1).
        """
        now = now or datetime.now(timezone.utc)

        # Step 1: validate.
        if not content or not content.strip():
            raise ValueError("message content is required")

        message = ListenerMessage(
            id=f"msg-{hashlib.sha256(f'{session_id}{client_id}{now}'.encode()).hexdigest()[:12]}",
            session_id=session_id,
            client_id=client_id,
            received_at=now,
            content=content,
        )

        # Step 2: rate limits.
        primary_topic = self._primary_topic(content)
        decision = self.rate_limiter.check(client_id, primary_topic, now)
        if not decision.allowed:
            message.moderation_status = ModerationStatus.REJECTED
            message.processing_status = ProcessingStatus.DECLINED
            message.metadata["rate_limit_reason"] = decision.reason
            self.messages[message.id] = message
            self._emit("listener.rate_limited", message, decision.reason)
            return message, "rate_limited"

        # Step 3: sanitize (bounded length; content preserved otherwise).
        message.content = message.content[:self.classifier.max_message_length]

        # Step 4: persist.
        self.messages[message.id] = message

        # Step 5: classify type.
        message.message_type = self._classify_type(content)
        message.topic_candidates = self._extract_topics(content)

        # Abuse assessment (§17.5) — folded into steps 5–7.
        assessment = self.classifier.classify(
            message,
            recent_hashes=list(self.recent_hashes),
        )
        message.moderation_status = assessment.moderation

        self.rate_limiter.record(client_id, primary_topic, now)
        self.recent_hashes.append(message.content_hash)

        # Steps 6–7: relevance and eligibility. A question is not
        # automatically answerable on air (§17.3).
        if assessment.should_reject:
            message.processing_status = ProcessingStatus.DECLINED
            self._emit("listener.rejected", message, "; ".join(assessment.reasons))
            return message, "rejected"

        if assessment.requires_review:
            message.processing_status = ProcessingStatus.DEFERRED
            self._emit("listener.flagged", message, "; ".join(assessment.reasons))
            return message, "flagged_for_review"

        # Step 8: add to the editorial queue — bounded by the quota (§17.5).
        if len(self.tasks) >= self.queue_quota:
            message.processing_status = ProcessingStatus.DEFERRED
            self._emit("listener.deferred", message, "queue at capacity")
            return message, "deferred"

        task = ResearchTask(
            id=f"task-{message.id}",
            message_id=message.id,
            question=content,
            topic_candidates=message.topic_candidates,
        )
        self.tasks.append(task)
        message.processing_status = ProcessingStatus.QUEUED
        self._emit("listener.queued", message, primary_topic)
        return message, "queued"

    def _emit(self, event_type: str, message: ListenerMessage,
              detail: str) -> None:
        if self.event_store is not None:
            self.event_store.emit(event_type, {
                "message_id": message.id,
                "session_id": message.session_id,
                "type": message.message_type.value,
                "detail": detail,
            })

    def _classify_type(self, content: str) -> MessageType:
        lowered = content.lower().strip()
        if lowered.endswith("?"):
            return MessageType.QUESTION
        if any(w in lowered for w in ("talk about", "cover", "report on",
                                      "more about", "explain")):
            return MessageType.TOPIC_REQUEST
        if any(w in lowered for w in ("clarify", "did you mean", "repeat")):
            return MessageType.CLARIFICATION
        if any(w in lowered for w in ("liked", "enjoyed", "boring", "good job")):
            return MessageType.FEEDBACK
        return MessageType.OTHER

    def _extract_topics(self, content: str) -> List[str]:
        """Candidate topics for matching against stories.

        Deterministic noun-phrase-free extraction: capitalized runs and
        quoted spans. Not entity resolution — the planner matches these
        candidates against stories.
        """
        import re
        topics: List[str] = []
        for match in re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", content):
            if match.lower() not in ("the", "i", "why", "how", "what"):
                topics.append(match)
        return list(dict.fromkeys(topics))[:5]

    @staticmethod
    def _primary_topic(content: str) -> str:
        import re
        match = re.search(r"\b([A-Z][a-z]+)\b", content)
        return match.group(1).lower() if match else "general"

    def link_response(self, message_id: str, segment_id: str) -> None:
        """Step 10: record the relationship between message and segment."""
        message = self.messages.get(message_id)
        if message is None:
            raise KeyError(message_id)
        message.response_segment_id = segment_id
        message.processing_status = ProcessingStatus.RESPONDED
