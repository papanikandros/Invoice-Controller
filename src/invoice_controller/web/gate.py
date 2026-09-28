"""Login throttling for the web UI's password gate.

The UI is reachable from the internet (behind the shared Caddy proxy, no outer basic-auth
since 2026-09-28), so the single-password login needs a brute-force brake: after
``MAX_ATTEMPTS`` failures inside ``WINDOW`` seconds an address is locked out for
``LOCKOUT`` seconds. State is in-process — the app is one uvicorn process — and a restart
clears it.

Addresses come from ``X-Forwarded-For`` when the proxy sets it, otherwise from the socket.
"""

from __future__ import annotations

import time

from starlette.requests import Request

MAX_ATTEMPTS = 5      # failures allowed per WINDOW before a lockout
WINDOW = 300          # 5 minutes
LOCKOUT = 900         # 15 minutes

_attempts: dict[str, list[float]] = {}
_locked: dict[str, float] = {}


def client_ip(request: Request | None, fallback: str = "-") -> str:
    """Real client address, honouring the proxy's forwarded header."""
    if request is None:
        return fallback
    try:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else fallback
    except (AttributeError, KeyError, TypeError):
        return fallback


def lock_seconds_left(ip: str) -> int:
    """Seconds remaining on this address' lockout, 0 when not locked."""
    until = _locked.get(ip, 0.0)
    left = until - time.time()
    if left <= 0:
        _locked.pop(ip, None)
        return 0
    return int(left) + 1


def record_failure(ip: str) -> int:
    """Count a failed attempt; returns the lockout seconds it triggered (0 if none)."""
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
    hits.append(now)
    _attempts[ip] = hits
    if len(hits) >= MAX_ATTEMPTS:
        _locked[ip] = now + LOCKOUT
        _attempts.pop(ip, None)
        return LOCKOUT
    return 0


def attempts_left(ip: str) -> int:
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
    return max(0, MAX_ATTEMPTS - len(hits))


def clear(ip: str) -> None:
    _attempts.pop(ip, None)
    _locked.pop(ip, None)
