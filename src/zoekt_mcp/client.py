"""Async HTTP client for a zoekt-webserver backend or M365 Zoekt Auth Gate.

Thin wrapper around the three zoekt-webserver endpoints we need:

* ``POST /api/search`` — run a zoekt query (requires the server to have
  been started with ``-rpc``, which registers the JSON API under
  ``/api/*``; without it, requests fall through to the HTML handlers).
* ``POST /api/list``   — enumerate indexed repositories (same gate).
* ``GET  /print?format=raw`` — fetch a file's raw contents as
  ``text/plain``.

Supports authenticated Zoekt web gateways via Bearer token and session cookie
injection, and detects HTML SSO sign-in responses on ``/print?format=raw`` so
unauthenticated gateway responses never masquerade as source code.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

import httpx

from .auth import SESSION_COOKIE_NAME

DEFAULT_BACKEND_URL = "http://localhost:6070"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ZoektBackendError(RuntimeError):
    """Raised when zoekt-webserver returns a non-2xx response or invalid JSON.

    The ``body`` attribute holds the raw response body (truncated) so callers
    can surface a useful message to the agent rather than a bare stack trace.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, body: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ZoektClient:
    """Async client for a zoekt-webserver instance or M365 Zoekt Auth Gate.

    Use as an async context manager so the underlying ``httpx.AsyncClient``
    is closed deterministically::

        async with ZoektClient("http://localhost:6070") as client:
            result = await client.search("lang:python def hello")
    """

    def __init__(
        self,
        backend_url: str = DEFAULT_BACKEND_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
        token_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.backend_url = backend_url.rstrip("/")
        self._timeout = timeout
        self._auth_token = auth_token
        self._token_provider = token_provider
        # Allow injecting a pre-built client for tests (respx, etc.).
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    def _auth_headers(self) -> dict[str, str]:
        token = self._auth_token
        if not token and self._token_provider is not None:
            token = self._token_provider()
        if not token:
            return {}
        return {
            "Authorization": f"Bearer {token}",
            "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        }

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        *,
        num_context_lines: int = 3,
        max_doc_display_count: int = 50,
        chunk_matches: bool = True,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a zoekt query. Returns the ``Result`` sub-object.

        ``query`` is a raw zoekt query string (e.g. ``"lang:python def hello"``).
        The caller is responsible for escaping/quoting where necessary.
        """
        opts: dict[str, Any] = {
            "NumContextLines": num_context_lines,
            "MaxDocDisplayCount": max_doc_display_count,
            "ChunkMatches": chunk_matches,
        }
        if extra_opts:
            opts.update(extra_opts)

        payload = {"Q": query, "Opts": opts}
        data = await self._post_json("/api/search", payload)
        # zoekt wraps the search result under a top-level "Result" key.
        result = data.get("Result", data)
        return dict(result) if isinstance(result, dict) else {}

    async def list_repos(self, query: str = "repo:.") -> dict[str, Any]:
        """List indexed repositories matching ``query``.

        Per zoekt's JSON API, the query for ``/api/list`` must contain only
        ``repo:`` atoms; the default ``"repo:."`` means "every repo".
        """
        payload = {"Q": query}
        data = await self._post_json("/api/list", payload)
        result = data.get("List", data)
        return dict(result) if isinstance(result, dict) else {}

    async def get_repo_metadata(self) -> dict[str, dict[str, Any]]:
        """Fetch optional repository enrichment metadata from ``GET /api/repo-metadata``.

        M365-gated Zoekt gateways (such as ``https://code.innodata.studio``) expose
        owner, ``pushed_at``, and pull-request statistics synced from GitHub. Bare
        ``zoekt-webserver`` instances do not implement this endpoint and return 404
        or HTML; in any non-200 or non-JSON case this method returns ``{}``.
        """
        headers = {"Accept": "application/json"}
        headers.update(self._auth_headers())
        try:
            response = await self._http.get(
                f"{self.backend_url}/api/repo-metadata",
                headers=headers,
            )
        except httpx.HTTPError:
            return {}

        if response.status_code != 200:
            return {}

        try:
            parsed = response.json()
        except ValueError:
            return {}

        if not isinstance(parsed, dict):
            return {}

        repos_raw = parsed.get("repos", parsed)
        if not isinstance(repos_raw, dict):
            return {}

        return {str(k): dict(v) for k, v in repos_raw.items() if isinstance(v, dict)}

    async def get_file(self, repo: str, path: str, branch: str = "HEAD") -> str:
        """Fetch raw file contents via ``/print?format=raw``.

        Returns the file body as a plain string. Raises
        :class:`ZoektBackendError` on any non-2xx response or when an SSO web
        gateway returns an HTML sign-in page instead of ``text/plain``.
        """
        params = {"r": repo, "f": path, "b": branch, "format": "raw"}
        headers = {"Accept": "text/plain, application/json"}
        headers.update(self._auth_headers())
        try:
            response = await self._http.get(
                f"{self.backend_url}/print",
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ZoektBackendError(f"GET /print failed: {exc}") from exc

        if response.status_code in (401, 403):
            msg = (
                f"GET /print returned {response.status_code} (Unauthorized — run 'zoekt-mcp login')"
            )
            raise ZoektBackendError(
                msg,
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        if response.status_code >= 400:
            raise ZoektBackendError(
                f"GET /print returned {response.status_code}",
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type and not path.lower().endswith((".html", ".htm")):
            raise ZoektBackendError(
                "GET /print returned text/html instead of raw source "
                "(gateway requires authentication — run 'zoekt-mcp login')",
                status_code=401,
                body=_truncate(response.text),
            )

        return response.text

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.backend_url}{path}"
        headers = {"Accept": "application/json"}
        headers.update(self._auth_headers())
        try:
            response = await self._http.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ZoektBackendError(f"POST {path} failed: {exc}") from exc

        if response.status_code in (401, 403):
            msg = (
                f"POST {path} returned {response.status_code} "
                "(Unauthorized — run 'zoekt-mcp login')"
            )
            raise ZoektBackendError(
                msg,
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        if response.status_code >= 400:
            raise ZoektBackendError(
                f"POST {path} returned {response.status_code}",
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        try:
            parsed = response.json()
        except ValueError as exc:
            raise ZoektBackendError(
                f"POST {path} returned invalid JSON",
                status_code=response.status_code,
                body=_truncate(response.text),
            ) from exc

        if not isinstance(parsed, dict):
            raise ZoektBackendError(
                f"POST {path} returned non-object JSON",
                status_code=response.status_code,
                body=_truncate(response.text),
            )
        return parsed


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars]"
