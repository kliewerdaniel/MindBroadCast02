"""API contracts.

Spec §19.5: "Internal domain models should not automatically become public API
schemas. The API layer should expose stable representations that can evolve
independently of database implementation details."

So this module defines its own response types. A field rename in the database
does not change the API contract, and a schema change that alters a field's
meaning gets a new API version rather than silently breaking clients.

Structured errors carry an error code, a human-readable message, a request
identifier, retryability, and field-level validation detail (§19.5).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

API_VERSION = "v1"


# ── §19.5 Structured errors ──────────────────────────────────────────


class ErrorCode(Enum):
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    CONFLICT = "conflict"
    INTERNAL = "internal_error"
    NOT_AVAILABLE = "not_available"


@dataclass
class FieldError:
    field: str
    message: str


@dataclass
class ApiError(Exception):
    """A structured API error (§19.5).

    Not a bare string: the client must be able to branch on the code and know
    whether a retry is safe.
    """
    code: ErrorCode
    message: str
    request_id: str = ""
    retryable: bool = False
    field_errors: List[FieldError] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "request_id": self.request_id,
                "retryable": self.retryable,
            }
        }
        if self.field_errors:
            payload["error"]["fields"] = [
                {"field": f.field, "message": f.message}
                for f in self.field_errors]
        return payload


def validation_error(message: str, field_errors=None, request_id: str = "") -> ApiError:
    return ApiError(ErrorCode.VALIDATION_ERROR, message, request_id,
                    retryable=False,
                    field_errors=field_errors or [])


def not_found(resource: str, request_id: str = "") -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, f"{resource} not found", request_id)


def rate_limited(retry_after_seconds: int = 60, request_id: str = "") -> ApiError:
    return ApiError(ErrorCode.RATE_LIMITED,
                    f"rate limit exceeded; retry after {retry_after_seconds}s",
                    request_id, retryable=True)


def unauthorized(request_id: str = "") -> ApiError:
    return ApiError(ErrorCode.UNAUTHORIZED, "authentication required",
                    request_id, retryable=False)


# ── Response envelope ────────────────────────────────────────────────


@dataclass
class ApiResponse:
    """A versioned response envelope.

    Every response carries the API version so a client can detect a contract
    drift (§19.5).
    """
    status: int = 200
    body: Any = None
    api_version: str = API_VERSION
    request_id: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def ok(cls, body: Any, request_id: str = "") -> "ApiResponse":
        return cls(status=200, body=body, request_id=request_id)

    @classmethod
    def created(cls, body: Any, request_id: str = "") -> "ApiResponse":
        return cls(status=201, body=body, request_id=request_id)

    @classmethod
    def no_content(cls, request_id: str = "") -> "ApiResponse":
        return cls(status=204, body=None, request_id=request_id)

    @classmethod
    def error(cls, error: ApiError, status: Optional[int] = None) -> "ApiResponse":
        statuses = {
            ErrorCode.VALIDATION_ERROR: 400,
            ErrorCode.UNAUTHORIZED: 401,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.METHOD_NOT_ALLOWED: 405,
            ErrorCode.CONFLICT: 409,
            ErrorCode.RATE_LIMITED: 429,
            ErrorCode.INTERNAL: 500,
            ErrorCode.NOT_AVAILABLE: 503,
        }
        return cls(
            status=status or statuses.get(error.code, 500),
            body=error.to_dict(),
            request_id=error.request_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"api_version": self.api_version}
        if self.body is not None:
            result["data"] = self.body
        result["request_id"] = self.request_id
        return result


# ── Domain response representations (§19.5: stable, not raw tables) ──


@dataclass
class StorySummary:
    """A stable public representation. Not the internal Story dataclass."""
    id: str
    title: str
    status: str
    unresolved_questions: int = 0
    last_material_update_at: Optional[str] = None


@dataclass
class ClaimSummary:
    id: str
    proposition: str
    claim_type: str
    epistemic_status: str


@dataclass
class EvidenceSummary:
    id: str
    evidence_type: str
    source_id: str
    excerpt: str
    provenance_status: str


@dataclass
class SourceSummary:
    id: str
    name: str
    source_type: str
    enabled: bool
    ownership_group: Optional[str] = None


@dataclass
class SegmentSummary:
    id: str
    status: str
    content_state: str
    story_id: str = ""
    persona_id: str = ""
    objective: str = ""
    interrupted: bool = False
    completed_its_point: Optional[bool] = None


@dataclass
class SessionSummary:
    id: str
    operating_mode: str
    playback_state: str
    current_segment_id: Optional[str] = None
    last_completed_segment_id: Optional[str] = None


@dataclass
class SystemStatus:
    """§18.1 question 3: what is the system currently observing and processing."""
    operating_mode: str
    health: str
    subsystems: Dict[str, str] = field(default_factory=dict)
    active_sources: int = 0
    articles_pending: int = 0
    segments_active: int = 0
    listeners_queued: int = 0


@dataclass
class HealthStatus:
    status: str = "ok"
    checked_at: Optional[str] = None
    checks: Dict[str, bool] = field(default_factory=dict)
