"""Microsoft 365 (Entra ID) FOCI & Web Gateway Authentication for ``zoekt-mcp``.

Allows engineers to authenticate once against an M365-gated Zoekt web gateway
(such as ``https://code.innodata.studio``) via:

1. ``zoekt-mcp login`` — automatically exchanges a local Microsoft 365 FOCI
   refresh token (if present in ``~/.hermes``) or initiates an interactive
   Microsoft 365 device-code flow via the Zoekt web gateway's ``/_auth/start``
   and ``/_auth/poll`` endpoints, then saves the 7-day signed session token to
   ``~/.config/zoekt-mcp/auth.json`` (mode ``0600``).
2. ``zoekt-mcp status`` — inspects the local token and verifies it against the
   gateway's ``/_auth/whoami`` endpoint.
3. ``zoekt-mcp logout`` — removes the persisted token file.
"""

from __future__ import annotations

import json
import os
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_AUTH_FILE = Path.home() / ".config" / "zoekt-mcp" / "auth.json"
SESSION_COOKIE_NAME = "zoekt_m365_session"

DEFAULT_FOCI_CACHE_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".hermes" / "foci_token_cache.json",
    Path.home() / ".hermes" / "profiles" / "dannybot" / "foci_token_cache.json",
    Path.home() / ".hermes" / "profiles" / "artbot" / "foci_token_cache.json",
)


@dataclass(frozen=True)
class StoredSession:
    backend_url: str
    session_token: str
    email: str
    name: str
    tid: str
    expires_at: int

    def is_valid(self, *, now: float | None = None, skew_seconds: int = 60) -> bool:
        current = time.time() if now is None else now
        return bool(self.session_token) and (self.expires_at > current + skew_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_url": self.backend_url,
            "session_token": self.session_token,
            "email": self.email,
            "name": self.name,
            "tid": self.tid,
            "expires_at": self.expires_at,
        }


def load_stored_session(
    auth_file: Path = DEFAULT_AUTH_FILE,
    *,
    now_fn: Callable[[], float] = time.time,
) -> StoredSession | None:
    """Load and validate a persisted :class:`StoredSession` from disk."""
    if not auth_file.is_file():
        return None
    try:
        raw = json.loads(auth_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        sess = StoredSession(
            backend_url=str(raw.get("backend_url") or ""),
            session_token=str(raw.get("session_token") or ""),
            email=str(raw.get("email") or ""),
            name=str(raw.get("name") or ""),
            tid=str(raw.get("tid") or ""),
            expires_at=int(raw.get("expires_at") or 0),
        )
    except (OSError, ValueError, TypeError):
        return None
    if not sess.is_valid(now=now_fn()):
        return None
    return sess


def save_stored_session(session: StoredSession, auth_file: Path = DEFAULT_AUTH_FILE) -> Path:
    """Persist a :class:`StoredSession` to disk with ``0600`` permissions."""
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(session.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.chmod(auth_file, 0o600)
    return auth_file


def clear_stored_session(auth_file: Path = DEFAULT_AUTH_FILE) -> bool:
    """Delete the persisted session file if it exists. Returns True if removed."""
    if auth_file.is_file():
        auth_file.unlink()
        return True
    return False


def extract_foci_refresh_token(
    explicit_path: Path | None = None,
    candidates: tuple[Path, ...] = DEFAULT_FOCI_CACHE_CANDIDATES,
) -> str | None:
    """Extract an MSAL FOCI refresh token from a local token cache file if available."""
    search_paths = (explicit_path,) if explicit_path is not None else candidates
    for path in search_paths:
        if path is None or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rt_map = data.get("RefreshToken")
            if isinstance(rt_map, dict):
                for entry in rt_map.values():
                    if isinstance(entry, dict) and isinstance(entry.get("secret"), str):
                        secret = str(entry["secret"]).strip()
                        if secret:
                            return secret
        except (OSError, ValueError, TypeError):
            continue
    return None


def resolve_auth_token(
    *,
    explicit_token: str | None = None,
    auth_file: Path = DEFAULT_AUTH_FILE,
    now_fn: Callable[[], float] = time.time,
) -> str | None:
    """Return the active session token from explicit flag/env or persisted auth file."""
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()
    sess = load_stored_session(auth_file, now_fn=now_fn)
    if sess is not None:
        return sess.session_token
    return None


async def exchange_refresh_token_at_gateway(
    backend_url: str,
    refresh_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> StoredSession:
    """Exchange a Microsoft 365 FOCI refresh token at ``POST /_auth/token``."""
    base = backend_url.rstrip("/")
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.post(
            f"{base}/_auth/token",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"POST /_auth/token returned {resp.status_code}: {resp.text}")
        data = resp.json()
        return StoredSession(
            backend_url=base,
            session_token=str(data["session_token"]),
            email=str(data.get("email") or ""),
            name=str(data.get("name") or ""),
            tid=str(data.get("tid") or ""),
            expires_at=int(data.get("expires_at") or (int(time.time()) + 86400 * 7)),
        )
    finally:
        if owns:
            await client.aclose()


async def run_device_code_login(
    backend_url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
    poll_interval: float = 2.0,
    max_polls: int = 120,
    no_browser: bool = False,
    browser_opener: Callable[[str], Any] = webbrowser.open,
    sleep_fn: Callable[[float], Any] | None = None,
    output_fn: Callable[[str], None] = print,
) -> StoredSession:
    """Run an interactive Microsoft 365 device-code login via the Zoekt web gateway."""
    import asyncio

    sleeper = sleep_fn or asyncio.sleep
    base = backend_url.rstrip("/")
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout)
    try:
        start_resp = await client.post(f"{base}/_auth/start")
        if start_resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to start gateway authentication ({start_resp.status_code}): "
                f"{start_resp.text}"
            )
        start_data = start_resp.json()
        flow_id = str(start_data["flow_id"])
        user_code = str(start_data["user_code"])
        verification_uri = str(
            start_data.get("verification_uri") or "https://microsoft.com/devicelogin"
        )

        output_fn(
            f"\nTo sign in to Zoekt Code Search ({base}):\n"
            f"  1. Open: {verification_uri}\n"
            f"  2. Enter code: {user_code}\n"
        )
        if not no_browser:
            try:
                browser_opener(verification_uri)
            except Exception:
                pass

        for _ in range(max_polls):
            await sleeper(poll_interval)
            poll_resp = await client.get(f"{base}/_auth/poll", params={"flow_id": flow_id})
            if poll_resp.status_code >= 400:
                raise RuntimeError(
                    f"Device login failed ({poll_resp.status_code}): {poll_resp.text}"
                )
            poll_data = poll_resp.json()
            status = poll_data.get("status")
            if status == "pending":
                continue
            if status == "authenticated":
                token = str(poll_data.get("session_token") or "")
                if not token:
                    cookie_tok = poll_resp.cookies.get(SESSION_COOKIE_NAME)
                    token = str(cookie_tok or "")
                if not token:
                    raise RuntimeError("Gateway did not return a session_token or cookie.")
                return StoredSession(
                    backend_url=base,
                    session_token=token,
                    email=str(poll_data.get("email") or ""),
                    name=str(poll_data.get("name") or ""),
                    tid=str(poll_data.get("tid") or ""),
                    expires_at=int(poll_data.get("expires_at") or (int(time.time()) + 86400 * 7)),
                )
            raise RuntimeError(f"Authentication error: {poll_data.get('error') or poll_data}")

        raise TimeoutError("Timed out waiting for Microsoft 365 device-code completion.")
    finally:
        if owns:
            await client.aclose()


async def check_gateway_whoami(
    backend_url: str,
    token: str | None,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Query ``GET /_auth/whoami`` on the Zoekt gateway to verify authentication."""
    base = backend_url.rstrip("/")
    owns = http_client is None
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={token}"
    client = http_client or httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.get(f"{base}/_auth/whoami", headers=headers)
        if resp.status_code >= 400:
            return {"authenticated": False, "status_code": resp.status_code, "body": resp.text}
        data: dict[str, Any] = resp.json()
        return data
    finally:
        if owns:
            await client.aclose()
