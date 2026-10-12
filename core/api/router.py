"""Framework-agnostic request routing.

Spec §19.1: "the architecture should not depend on a specific web framework."

So the router here is plain: a table of (method, path) -> handler, where a
handler receives a parsed request and returns an `ApiResponse`. A FastAPI
adapter in `server.py` wires this to HTTP; nothing in this module imports a web
framework.

§19.2: "The API must not expose raw internal database access or unrestricted
administrative operations to an unauthenticated network client." So admin
endpoints are marked, and the auth check is a property of the route, not the
handler's memory.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .contracts import (
    ApiError, ApiResponse, ErrorCode, not_found, unauthorized, validation_error,
)


@dataclass
class ApiRequest:
    method: str
    path: str
    query: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    authenticated: bool = False
    request_id: str = ""
    client_id: str = ""

    def query_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self.query.get(name, default))
        except (TypeError, ValueError):
            return default


# A handler: (ApiRequest, path params) -> ApiResponse
Handler = Callable[[ApiRequest, Dict[str, str]], ApiResponse]


def _compile(pattern: str):
    # Convert `/stories/{id}` into a regex with a named group.
    parts = pattern.strip("/").split("/")
    regex_parts = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            regex_parts.append(f"(?P<{part[1:-1]}>[^/]+)")
        else:
            regex_parts.append(re.escape(part))
    return re.compile("^/" + "/".join(regex_parts) + "$")


class Route:
    """A single endpoint (§19.2)."""

    def __init__(self, method: str, pattern: str, handler: Handler,
                 requires_auth: bool = False, summary: str = ""):
        self.method = method.upper()
        self.pattern = pattern
        self.handler = handler
        self.requires_auth = requires_auth
        self.summary = summary
        self._regex = _compile(pattern)

    def matches(self, method: str, path: str) -> Optional[Dict[str, str]]:
        if method.upper() != self.method:
            return None
        match = self._regex.match(path)
        return match.groupdict() if match else None


class Router:
    """Dispatches requests to handlers. No framework dependency."""

    def __init__(self):
        self.routes: List[Route] = []

    def add(self, method: str, pattern: str, handler: Handler,
            requires_auth: bool = False, summary: str = "") -> Route:
        route = Route(method, pattern, handler, requires_auth, summary)
        self.routes.append(route)
        return route

    def get(self, pattern: str, **kwargs) -> Callable:
        def decorator(handler: Handler) -> Handler:
            self.add("GET", pattern, handler, **kwargs)
            return handler
        return decorator

    def post(self, pattern: str, **kwargs) -> Callable:
        def decorator(handler: Handler) -> Handler:
            self.add("POST", pattern, handler, **kwargs)
            return handler
        return decorator

    def patch(self, pattern: str, **kwargs) -> Callable:
        def decorator(handler: Handler) -> Handler:
            self.add("PATCH", pattern, handler, **kwargs)
            return handler
        return decorator

    def resolve(self, method: str, path: str) -> Optional[Tuple[Route, Dict[str, str]]]:
        """Find the route, or None. Path-level 404, not method-level."""
        path_matched_any_method = False
        for route in self.routes:
            params = route.matches(method, path)
            if params is not None:
                return route, params
        return None

    def dispatch(self, request: ApiRequest) -> ApiResponse:
        resolved = self.resolve(request.method, request.path)
        if resolved is None:
            # Distinguish 404 (no such path) from 405 (path exists, wrong method).
            path_exists = any(
                r._regex.match(request.path) for r in self.routes)
            if path_exists:
                return ApiResponse.error(ApiError(
                    ErrorCode.METHOD_NOT_ALLOWED,
                    f"{request.method} not allowed on {request.path}",
                    request.request_id))
            return ApiResponse.error(not_found(
                f"{request.method} {request.path}", request.request_id))

        route, params = resolved
        if route.requires_auth and not request.authenticated:
            return ApiResponse.error(unauthorized(request.request_id))

        try:
            return route.handler(request, params)
        except ApiError as error:
            error.request_id = error.request_id or request.request_id
            return ApiResponse.error(error)
        except ValueError as error:
            return ApiResponse.error(validation_error(
                str(error), request_id=request.request_id))

    def endpoints(self) -> List[Dict[str, Any]]:
        """The §19.2 endpoint table, for introspection and tests."""
        return [
            {
                "method": r.method,
                "path": r.pattern,
                "requires_auth": r.requires_auth,
                "summary": r.summary,
            }
            for r in self.routes
        ]
