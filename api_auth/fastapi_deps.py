"""FastAPI glue: `Depends(require("scope"))` on a route, a registry that can be swapped in tests, fail-closed."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from fastapi import HTTPException, Request

from .principals import AuthRegistry, Principal

log = logging.getLogger("eternal.auth")
_lock = threading.Lock()
_registry: Optional[AuthRegistry] = None


def get_registry() -> AuthRegistry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = AuthRegistry.from_env()
        return _registry


def set_registry(reg: Optional[AuthRegistry]) -> None:
    """Install (or, with None, reset to 'reload from env') the process-wide registry. Tests and `serve` scripts use this."""
    global _registry
    with _lock:
        _registry = reg


def authenticate(request: Request) -> Principal:
    reg = get_registry()
    client = request.client.host if request.client else "?"
    if not reg.configured:
        raise HTTPException(503, "authentication is not configured (set ETERNAL_AUTH_FILE); refusing protected route")
    if reg.throttled(client):
        raise HTTPException(429, "too many failed authentication attempts; try again later")
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    p = reg.authenticate(token, client)
    if p is None:
        log.warning("auth failure from %s on %s", client, request.url.path)
        raise HTTPException(401, "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = p
    return p


def require(scope: str) -> Callable[[Request], Principal]:
    def dep(request: Request) -> Principal:
        p = authenticate(request)
        if scope not in p.scopes:
            log.warning("principal %s lacks scope %s for %s", p.id, scope, request.url.path)
            raise HTTPException(403, f"principal '{p.id}' lacks scope '{scope}'")
        return p
    dep.__name__ = f"require_{scope.replace(':', '_')}"
    return dep
