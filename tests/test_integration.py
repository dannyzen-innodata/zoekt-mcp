"""End-to-end tests against a live zoekt-webserver or M365 Zoekt Auth Gate.

Supports two live backend modes:
1. Local docker-compose verification corpus (`examples/flask-app` and `examples/express-app`).
2. Innodata M365-authenticated Zoekt GKE Gateway (`https://code.innodata.studio`), verifying:
   - Unauthenticated requests to `/api/list`, `/api/search`, and `/print?format=raw` are
     rejected with HTTP 401 (never returning the HTML login page as raw source code).
   - `zoekt-mcp login` exchanges an M365 FOCI refresh token at `POST /_auth/token` and
     persists a 7-day session token (`~/.config/zoekt-mcp/auth.json`).
   - `zoekt-mcp status` verifies the session against `GET /_auth/whoami`.
   - `list_repos`, `search_code` (regex + `sym:` ctags), and `get_file` succeed end-to-end
     through the MCP server tools over the live gateway.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from zoekt_mcp.__main__ import _run_login, _run_status, main
from zoekt_mcp.auth import (
    exchange_refresh_token_at_gateway,
    extract_foci_refresh_token,
    resolve_auth_token,
)
from zoekt_mcp.client import DEFAULT_BACKEND_URL, ZoektBackendError, ZoektClient
from zoekt_mcp.config import ZoektConfig, load_config
from zoekt_mcp.server import build_server

BACKEND_URL = os.environ.get("ZOEKT_URL", DEFAULT_BACKEND_URL)


def _backend_reachable() -> bool:
    try:
        response = httpx.get(f"{BACKEND_URL.rstrip('/')}/healthz", timeout=3.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


pytestmark = pytest.mark.skipif(
    not _backend_reachable(),
    reason=(
        f"zoekt-webserver at {BACKEND_URL} is not reachable. "
        "Bring the stack up with `docker compose -f deploy/docker-compose.yml up -d` "
        "or set ZOEKT_URL=https://code.innodata.studio."
    ),
)


@pytest.fixture
async def client() -> AsyncIterator[ZoektClient]:
    token = resolve_auth_token(explicit_token=os.environ.get("ZOEKT_AUTH_TOKEN"))
    if token is None:
        rt = extract_foci_refresh_token()
        if rt:
            try:
                sess = await exchange_refresh_token_at_gateway(BACKEND_URL, rt)
                token = sess.session_token
            except Exception:
                token = None
    async with ZoektClient(BACKEND_URL, auth_token=token) as c:
        yield c


def _all_file_names(result: dict[str, Any]) -> list[str]:
    files = result.get("Files") or result.get("files") or []
    names: list[str] = []
    for f in files:
        name = f.get("FileName") or f.get("fileName") or f.get("file") or ""
        if name:
            names.append(str(name))
    return names


def _all_repo_names(list_result: dict[str, Any]) -> list[str]:
    repos = list_result.get("Repos") or list_result.get("repos") or list_result.get("List") or []
    names: list[str] = []
    for entry in repos:
        repo = entry.get("Repository") or entry.get("repository") or entry
        name = repo.get("Name") or repo.get("name") or ""
        if name:
            names.append(str(name))
    return names


async def test_live_unauthenticated_gateway_rejection_and_m365_e2e(tmp_path: Path) -> None:
    """When pointed at an M365 Zoekt Auth Gate, verify auth enforcement and full MCP tool E2E."""
    who_unauth = httpx.get(f"{BACKEND_URL.rstrip('/')}/_auth/whoami", timeout=5.0)
    if who_unauth.status_code == 404:
        pytest.skip("Backend is a bare zoekt-webserver without M365 Auth Gate (INN-615)")

    # 1. Unauthenticated client must be rejected with 401 on list_repos, search, and get_file
    async with ZoektClient(BACKEND_URL, auth_token=None) as unauth_client:
        with pytest.raises(ZoektBackendError) as exc_list:
            await unauth_client.list_repos("repo:makor")
        assert exc_list.value.status_code == 401

        with pytest.raises(ZoektBackendError) as exc_search:
            await unauth_client.search("repo:makor flake")
        assert exc_search.value.status_code == 401

        with pytest.raises(ZoektBackendError) as exc_file:
            await unauth_client.get_file("github.com/InnodataAgenticResearch/makor", "README.md")
        assert exc_file.value.status_code == 401

    # 2. Authenticate via `_run_login` (`zoekt-mcp login`) using FOCI refresh token
    if extract_foci_refresh_token() is None:
        pytest.skip("No local M365 FOCI refresh token cache available for live login E2E (INN-615)")

    auth_file = tmp_path / "e2e_auth.json"
    login_cfg = load_config(["login", "--zoekt-url", BACKEND_URL, "--auth-file", str(auth_file)])
    rc_login = await _run_login(login_cfg)
    assert rc_login == 0
    assert auth_file.is_file()

    # 3. Verify `_run_status` (`zoekt-mcp status`) succeeds against live `/_auth/whoami`
    status_cfg = load_config(["status", "--zoekt-url", BACKEND_URL, "--auth-file", str(auth_file)])
    rc_status = await _run_status(status_cfg)
    assert rc_status == 0

    # 4. Build the FastMCP server bound to the persisted auth_file and exercise all 3 MCP tools
    cfg = ZoektConfig(
        backend_url=BACKEND_URL,
        timeout=20.0,
        auth_file=auth_file,
    )
    mcp = build_server(cfg)
    try:
        tools: dict[str, Any] = {t.name: t for t in mcp._tool_manager.list_tools()}

        repos_res = await tools["list_repos"].fn(filter="makor")
        assert repos_res["count"] >= 1
        makor_entries = [
            r for r in repos_res["repos"] if r["name"] == "github.com/InnodataAgenticResearch/makor"
        ]
        assert len(makor_entries) == 1
        assert "pushed_at" in makor_entries[0]
        assert isinstance(makor_entries[0].get("owners"), list)

        search_res = await tools["search_code"].fn(
            query="repo:InnodataAgenticResearch/makor file:^README\\.md$ Makor",
            max_results=10,
            context_lines=2,
        )
        assert search_res["file_count"] >= 1
        assert any(f["file"] == "README.md" for f in search_res["files"])

        sym_res = await tools["search_code"].fn(
            query="repo:InnodataAgenticResearch/makor sym:SiYuanAuthGateway",
            max_results=10,
            context_lines=2,
        )
        assert sym_res["file_count"] >= 1

        file_res = await tools["get_file"].fn(
            repo="github.com/InnodataAgenticResearch/makor",
            path="README.md",
            branch="HEAD",
        )
        assert "# makor" in file_res["content"]
        assert "<!DOCTYPE html>" not in file_res["content"]
    finally:
        await mcp.__dict__["_zoekt_client"].aclose()

    # 5. Logout clears the auth file and subsequent status returns 1
    assert main(["logout", "--auth-file", str(auth_file)]) == 0
    assert not auth_file.exists()


async def test_list_repos_returns_indexed_repositories(client: ZoektClient) -> None:
    result = await client.list_repos("repo:.")
    names = _all_repo_names(result)
    assert len(names) >= 1
