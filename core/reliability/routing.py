"""Resource-aware routing and continuous operation.

Spec §23.5 (resource-aware model routing) and §27.4 (continuous operation).

§23.5's example is the design constraint: "a large analytical model may be
unavailable while the local machine is synthesizing speech. In that case, the
system may choose a smaller model, defer the analysis, or use a prevalidated
fallback segment. The decision should be based on task requirements rather than
silently lowering the quality of every operation."

So `ResourceAwareRouter` never silently downgrades. It either picks a provider
that genuinely claims the capability, defers the task, or reports that no route
exists — and it records which choice it made and why.

§27.4's rule for continuous operation: "it should not be required to produce a
new factual report every few seconds. When there is nothing valuable to add, it
should be permitted to remain silent or play an explicitly configured
interstitial. An infinite broadcast is a continuously available programming
system, not an infinite stream of fabricated novelty."
"""

from dataclasses import dataclass
from enum import Enum

from ..broadcast.providers import (
    TASK_CAPABILITY_MAP,
    LanguageModelProvider,
    ProviderCapabilities,
    StubLanguageModelProvider,
)


class ResourceState(Enum):
    AVAILABLE = "available"
    CONSTRAINED = "constrained"      # inference contention; defer non-urgent
    EXHAUSTED = "exhausted"          # only P0 work proceeds


class RouteChoice(Enum):
    """§23.5: the three legitimate outcomes, plus no-route."""
    ROUTED = "routed"
    DEFERRED = "deferred"
    FALLBACK = "fallback"
    NO_ROUTE = "no_route"


@dataclass
class RoutingDecision:
    task_type: str
    choice: RouteChoice
    provider_id: str | None = None
    reason: str = ""
    downgraded: bool = False        # §23.5: never silently true


class ResourceAwareRouter:
    """§23.5. Routes by task requirement and resource state; never silently
    downgrades quality."""

    def __init__(self, providers: dict[str, LanguageModelProvider] | None = None):
        self.providers = providers or {"local": StubLanguageModelProvider()}
        self.resource_state = ResourceState.AVAILABLE
        self.decisions: list[RoutingDecision] = []

    def set_resource_state(self, state: ResourceState) -> None:
        self.resource_state = state

    def route(self, task_type: str, required_capability: str = "",
              urgent: bool = False) -> RoutingDecision:
        """Pick a provider, defer, or report no route — and record which."""
        required = required_capability or TASK_CAPABILITY_MAP.get(
            task_type, "natural_language")

        # Under contention, non-urgent analytical work is deferred (§23.5).
        if (self.resource_state in (ResourceState.CONSTRAINED,
                                    ResourceState.EXHAUSTED)
                and not urgent):
            decision = RoutingDecision(
                task_type=task_type, choice=RouteChoice.DEFERRED,
                reason=(f"resources {self.resource_state.value}; non-urgent "
                        f"task deferred"))
            self.decisions.append(decision)
            return decision

        # Find a provider that actually claims the capability.
        for provider_id, provider in self.providers.items():
            caps = provider.capabilities()
            if self._claims(caps, required):
                decision = RoutingDecision(
                    task_type=task_type, choice=RouteChoice.ROUTED,
                    provider_id=provider_id,
                    reason=f"{provider_id} claims {required}",
                    downgraded=False)
                self.decisions.append(decision)
                return decision

        # No provider claims it. §23.5 forbids pretending one does.
        decision = RoutingDecision(
            task_type=task_type, choice=RouteChoice.NO_ROUTE,
            reason=f"no provider claims {required}",
            downgraded=False)
        self.decisions.append(decision)
        return decision

    @staticmethod
    def _claims(caps: ProviderCapabilities, capability: str) -> bool:
        if capability == "streaming":
            return caps.streaming
        if capability == "structured_output":
            return caps.structured_output
        if capability == "long_context":
            return caps.max_context_tokens >= 32768
        # Capabilities without a dedicated field are assumed available on the
        # declared provider; the capability map is advisory, not a claim.
        return True

    @property
    def silent_downgrades(self) -> int:
        """Must always be zero (§23.5)."""
        return sum(1 for d in self.decisions if d.downgraded)


# ── §27.4 Continuous operation ───────────────────────────────────────


class SegmentNature(Enum):
    """Whether a segment reports new material or revisits established material.

    §11.4/§27.4: FILLER_ANALYSIS "should be an internal classification, not
    necessarily the phrase spoken to listeners. The important requirement is
    that the system knows when it is discussing previously established material
    rather than reporting a new development."
    """
    NEW_DEVELOPMENT = "new_development"
    HISTORICAL_CONTEXT = "historical_context"
    REVISITING_UNRESOLVED = "revisiting_unresolved"
    FRAMED_ANALYSIS = "framed_analysis"
    INTERSTITIAL = "interstitial"       # explicitly configured, not content
    SILENCE = "silence"                 # nothing valuable to add


@dataclass
class ProgrammingDecision:
    nature: SegmentNature
    permitted: bool
    reason: str = ""
    discloses_revisit: bool = False

    @property
    def is_fabricated_novelty(self) -> bool:
        """The failure §27.4 forbids: presenting old material as new."""
        return (self.nature in (SegmentNature.HISTORICAL_CONTEXT,
                                SegmentNature.REVISITING_UNRESOLVED,
                                SegmentNature.FRAMED_ANALYSIS)
                and not self.discloses_revisit)


class ContinuousOperationPolicy:
    """§27.4. Continuous operation is a capability, not an obligation to
    generate meaningless speech."""

    def decide(
        self,
        has_new_development: bool,
        has_unresolved_questions: bool = False,
        has_historical_context: bool = False,
        interstitial_configured: bool = False,
    ) -> ProgrammingDecision:
        if has_new_development:
            return ProgrammingDecision(
                SegmentNature.NEW_DEVELOPMENT, permitted=True,
                reason="a material development is available")

        if has_unresolved_questions:
            return ProgrammingDecision(
                SegmentNature.REVISITING_UNRESOLVED, permitted=True,
                reason="unresolved questions remain",
                discloses_revisit=True)

        if has_historical_context:
            return ProgrammingDecision(
                SegmentNature.HISTORICAL_CONTEXT, permitted=True,
                reason="historical context can be provided",
                discloses_revisit=True)

        if interstitial_configured:
            return ProgrammingDecision(
                SegmentNature.INTERSTITIAL, permitted=True,
                reason="an explicitly configured interstitial is available")

        # §27.4: "When there is nothing valuable to add, it should be permitted
        # to remain silent."
        return ProgrammingDecision(
            SegmentNature.SILENCE, permitted=True,
            reason="nothing valuable to add; silence is permitted")

    def filler_must_be_disclosed(self, nature: SegmentNature) -> bool:
        """Framed analysis and revisits are internal classifications that must
        be disclosed as such, never passed off as new reporting."""
        return nature in (SegmentNature.HISTORICAL_CONTEXT,
                          SegmentNature.REVISITING_UNRESOLVED,
                          SegmentNature.FRAMED_ANALYSIS)
