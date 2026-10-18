"""Operator controls.

Spec §18.6.

The rule that shapes this module: "Actions that affect persistent policy or data
must have explicit confirmation and audit records where appropriate."

So every control is one of three kinds:

- **Immediate** — playback-affecting, reversible, no confirmation (mute, skip).
- **Policy** — changes persistent configuration, requires confirmation and
  writes an audit record (persona config, source toggle, editorial priority).
- **Operational** — system actions, audited but not gated (refresh, export).

The distinction is not cosmetic. An operator muting playback and an operator
disabling a source are different risk classes, and treating them the same either
annoys or under-protects.

§18.7's "not color alone" is enforced here too: every status this module reports
carries a text/state field, never only a color.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ControlKind(Enum):
    IMMEDIATE = "immediate"      # reversible, no confirmation
    POLICY = "policy"            # persistent; confirmation + audit
    OPERATIONAL = "operational"  # audited, not gated


class ControlStatus(Enum):
    """§18.7: state is text, never color alone."""
    SUCCEEDED = "succeeded"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DENIED = "denied"
    FAILED = "failed"
    NOT_PERMITTED = "not_permitted"


@dataclass
class AuditRecord:
    """§18.6: an audit record for actions affecting persistent state."""
    id: str
    action: str
    actor: str
    kind: ControlKind
    confirmed: bool
    detail: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ControlResult:
    status: ControlStatus
    action: str
    message: str = ""
    audit_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)


class ConfirmationRequired(Exception):
    """Raised when a POLICY control is invoked without confirmation."""

    def __init__(self, action: str, message: str):
        super().__init__(message)
        self.action = action


# The §18.6 control set, each classified.
CONTROL_KINDS: dict[str, ControlKind] = {
    "start_broadcast": ControlKind.OPERATIONAL,
    "stop_broadcast": ControlKind.OPERATIONAL,
    "pause_generation": ControlKind.IMMEDIATE,
    "mute_playback": ControlKind.IMMEDIATE,
    "skip_segment": ControlKind.IMMEDIATE,
    "request_topic": ControlKind.IMMEDIATE,
    "change_persona_config": ControlKind.POLICY,
    "toggle_source": ControlKind.POLICY,
    "adjust_editorial_priority": ControlKind.POLICY,
    "review_corrections": ControlKind.OPERATIONAL,
    "trigger_source_refresh": ControlKind.OPERATIONAL,
    "export_transcripts": ControlKind.OPERATIONAL,
    "switch_operating_mode": ControlKind.POLICY,
}


class OperatorConsole:
    """Executes the §18.6 controls with confirmation and audit semantics."""

    def __init__(self):
        self._audit: list[AuditRecord] = []
        self._audit_counter = 0
        self._state: dict[str, Any] = {
            "broadcast_running": False,
            "generation_paused": False,
            "muted": False,
            "operating_mode": "live",
        }
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {}

    def register_handler(self, action: str,
                         handler: Callable[..., dict[str, Any]]) -> None:
        self._handlers[action] = handler

    def execute(self, action: str, actor: str = "operator",
                confirmed: bool = False, **kwargs) -> ControlResult:
        """Run a control, enforcing confirmation and audit by kind."""
        kind = CONTROL_KINDS.get(action)
        if kind is None:
            return ControlResult(
                ControlStatus.NOT_PERMITTED, action,
                f"unknown control '{action}'")

        # POLICY controls require explicit confirmation (§18.6).
        if kind is ControlKind.POLICY and not confirmed:
            audit_id = self._audit_action(
                action, actor, kind, confirmed=False, detail=kwargs)
            raise ConfirmationRequired(
                action,
                f"'{action}' affects persistent policy; confirmation required")

        handler = self._handlers.get(action)
        if handler is None:
            # A control with no wired handler still executes its state effect
            # and audits; the side effect is delegated elsewhere.
            state_update = {}
        else:
            try:
                state_update = handler(**kwargs) or {}
            except Exception as error:
                audit_id = self._audit_action(
                    action, actor, kind, confirmed=confirmed,
                    detail={"error": str(error)})
                return ControlResult(
                    ControlStatus.FAILED, action, str(error), audit_id)

        self._state.update(state_update)
        audit_id = self._audit_action(
            action, actor, kind, confirmed=confirmed,
            detail={**kwargs, **state_update})
        return ControlResult(
            ControlStatus.SUCCEEDED, action,
            f"{action} executed", audit_id, dict(self._state))

    def _audit_action(self, action: str, actor: str, kind: ControlKind,
                      confirmed: bool, detail: dict[str, Any]) -> str:
        self._audit_counter += 1
        record = AuditRecord(
            id=f"audit-{self._audit_counter:06d}",
            action=action, actor=actor, kind=kind,
            confirmed=confirmed, detail=detail,
        )
        self._audit.append(record)
        return record.id

    def state(self) -> dict[str, Any]:
        """§18.7: state is text and explicit, never color-encoded."""
        return dict(self._state)

    def audit_log(self, action: str | None = None) -> list[AuditRecord]:
        if action is None:
            return list(self._audit)
        return [a for a in self._audit if a.action == action]

    def failed_tasks_view(self, failures: list[dict[str, Any]]
                          ) -> list[dict[str, Any]]:
        """§18.6: inspect failed tasks.

        §18.7: status carries a text field, so a renderer cannot rely on color.
        """
        return [
            {
                "task_id": f.get("id", ""),
                "status_text": "failed",          # explicit, not a color
                "reason": f.get("reason", ""),
                "retryable": bool(f.get("retryable", False)),
            }
            for f in failures
        ]


# ── §18.7 Accessibility data model ───────────────────────────────────


@dataclass
class AccessibilityPreferences:
    """§18.7. Preferences the frontend must honor.

    These are data-contract requirements: a renderer that ignores them is
    non-compliant, and the contract makes that checkable.
    """
    keyboard_navigation: bool = True
    visible_focus_indicators: bool = True
    screen_reader_labels: bool = True
    transcript_without_audio: bool = True
    adjustable_text_size: bool = True
    clear_error_states: bool = True
    respect_reduced_motion: bool = True
    audio_controls: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyboard_navigation": self.keyboard_navigation,
            "visible_focus_indicators": self.visible_focus_indicators,
            "screen_reader_labels": self.screen_reader_labels,
            "transcript_without_audio": self.transcript_without_audio,
            "adjustable_text_size": self.adjustable_text_size,
            "clear_error_states": self.clear_error_states,
            "respect_reduced_motion": self.respect_reduced_motion,
            "audio_controls": self.audio_controls,
        }


def status_is_not_color_encoded(status: dict[str, Any]) -> bool:
    """§18.7: "The visual design should not rely exclusively on color to
    communicate whether a segment is approved, disputed, or failed."

    A status payload is compliant only if it carries a textual state field
    alongside any color hint.
    """
    text_fields = ("status_text", "state", "status", "label", "text")
    return any(field in status for field in text_fields)
