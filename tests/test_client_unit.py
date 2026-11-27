"""Unit tests for :mod:`zoekt_mcp.client`, :mod:`zoekt_mcp.auth`, and :mod:`zoekt_mcp.__main__`.

All HTTP traffic is mocked with respx so these tests run deterministically offline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import zoekt_mcp
from zoekt_mcp.__main__ import main
from zoekt_mcp.auth import (
    SESSION_COOKIE_NAME,
    StoredSession,
    check_gateway_whoami,
    clear_stored_session,
    exchange_refresh_token_at_gateway,
    extract_foci_refresh_token,
    load_stored_session,
    resolve_auth_token,
    run_device_code_login,
    save_stored_session,
)
from zoekt_mcp.client import (
    DEFAULT_BACKEND_URL,
    ZoektBackendError,
    ZoektClient,
    _truncate,
)
from zoekt_mcp.config import load_config

SEARCH_URL = f"{DEFAULT_BACKEND_URL}/api/search"
LIST_URL = f"{DEFAULT_BACKEND_URL}/api/list"
PRINT_URL = f"{DEFAULT_BACKEND_URL}/print"


@pytest.fixture
async def client() -> AsyncIterator[ZoektClient]:
    async with ZoektClient() as c:
        yield c


def test_version_and_truncate() -> None:
    assert zoekt_mcp.__version__ == "0.4.1"
    assert _truncate("short", limit=10) == "short"
    long_txt = "x" * 25
    assert _truncate(long_txt, limit=10) == "xxxxxxxxxx... [truncated 15 chars]"


def test_load_config_flags_and_subcommands(tmp_path: Path) -> None:
    cfg_default = load_config([])
    assert cfg_default.subcommand == "serve"
    assert cfg_default.backend_url == DEFAULT_BACKEND_URL

    auth_p = tmp_path / "auth.json"
    foci_p = tmp_path / "foci.json"
    cfg_login = load_config(
        [
            "login",
            "--zoekt-url",
            "https://code.innodata.studio",
            "--timeout",
            "12.5",
            "--token",
            "tok-abc",
            "--auth-file",
            str(auth_p),
            "--foci-cache",
            str(foci_p),
            "--no-browser",
        ]
    )
    assert cfg_login.subcommand == "login"
    assert cfg_login.backend_url == "https://code.innodata.studio"
    assert cfg_login.timeout == 12.5
    assert cfg_login.auth_token == "tok-abc"
    assert cfg_login.auth_file == auth_p
    assert cfg_login.foci_cache == foci_p
    assert cfg_login.no_browser is True


def test_stored_session_and_foci_extraction(tmp_path: Path) -> None:
    auth_file = tmp_path / "sub" / "auth.json"
    assert load_stored_session(auth_file) is None
    assert clear_stored_session(auth_file) is False

    # Invalid JSON and non-dict JSON
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text("{bad json", encoding="utf-8")
    assert load_stored_session(auth_file) is None
    auth_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_stored_session(auth_file) is None

    # Expired session
    expired = StoredSession(
        backend_url="https://zoekt.example.com",
        session_token="tok-exp",
        email="drosen@innodata.com",
        name="Danny",
        tid="be92c534",
        expires_at=100,
    )
    save_stored_session(expired, auth_file)
    assert load_stored_session(auth_file, now_fn=lambda: 200.0) is None

    # Valid session
    valid = StoredSession(
        backend_url="https://zoekt.example.com",
        session_token="tok-valid",
        email="drosen@innodata.com",
        name="Danny",
        tid="be92c534",
        expires_at=5000,
    )
    save_stored_session(valid, auth_file)
    loaded = load_stored_session(auth_file, now_fn=lambda: 1000.0)
    assert loaded == valid
    assert valid.is_valid(now=1000.0) is True
    assert resolve_auth_token(explicit_token="  explicit-1  ", auth_file=auth_file) == "explicit-1"
    assert (
        resolve_auth_token(explicit_token=None, auth_file=auth_file, now_fn=lambda: 1000.0)
        == "tok-valid"
    )
    assert clear_stored_session(auth_file) is True
    assert (
        resolve_auth_token(explicit_token=None, auth_file=auth_file, now_fn=lambda: 1000.0) is None
    )

    # FOCI extraction
    missing_foci = tmp_path / "missing.json"
    bad_foci = tmp_path / "bad.json"
    bad_foci.write_text("{not-json", encoding="utf-8")
    empty_foci = tmp_path / "empty.json"
    empty_foci.write_text(json.dumps({"RefreshToken": {"k1": {"secret": "  "}, "k2": "not-dict"}}))
    non_dict_rt = tmp_path / "nondict.json"
    non_dict_rt.write_text(json.dumps({"RefreshToken": []}))
    good_foci = tmp_path / "good.json"
    good_foci.write_text(json.dumps({"RefreshToken": {"k1": {"secret": "rt-secret-999"}}}))

    assert (
        extract_foci_refresh_token(
            candidates=(missing_foci, bad_foci, empty_foci, non_dict_rt, good_foci)
        )
        == "rt-secret-999"
    )
    assert extract_foci_refresh_token(explicit_path=missing_foci) is None


@respx.mock
async def test_exchange_refresh_token_and_whoami() -> None:
    base = "https://code.innodata.studio"
    respx.post(f"{base}/_auth/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "authenticated",
                "session_token": "sess-123",
                "email": "drosen@innodata.com",
                "name": "Danny Rosen",
                "tid": "be92c534-7b60-4630-bc01-ec5122548d47",
                "expires_at": 9999999999,
            },
        )
    )
    sess = await exchange_refresh_token_at_gateway(base, "rt-abc")
    assert sess.session_token == "sess-123"
    assert sess.email == "drosen@innodata.com"

    # Error on /_auth/token with injected client
    respx.post(f"{base}/_auth/token").mock(return_value=httpx.Response(401, text="bad token"))
    async with httpx.AsyncClient() as custom_http:
        with pytest.raises(RuntimeError, match="POST /_auth/token returned 401"):
            await exchange_refresh_token_at_gateway(base, "rt-bad", http_client=custom_http)

    # check_gateway_whoami success & failure & without token
    respx.get(f"{base}/_auth/whoami").mock(
        return_value=httpx.Response(
            200, json={"authenticated": True, "email": "drosen@innodata.com"}
        )
    )
    who_ok = await check_gateway_whoami(base, "sess-123")
    assert who_ok["authenticated"] is True

    respx.get(f"{base}/_auth/whoami").mock(return_value=httpx.Response(401, text="unauth"))
    async with httpx.AsyncClient() as custom_http:
        who_err = await check_gateway_whoami(base, None, http_client=custom_http)
        assert who_err["authenticated"] is False
        assert who_err["status_code"] == 401


@respx.mock
async def test_run_device_code_login_flows() -> None:
    base = "https://code.innodata.studio"
    opened_urls: list[str] = []
    printed: list[str] = []

    async def fast_sleep(_: float) -> None:
        return None

    # 1. Start failure
    respx.post(f"{base}/_auth/start").mock(return_value=httpx.Response(502, text="entra down"))
    with pytest.raises(RuntimeError, match="Failed to start gateway authentication"):
        await run_device_code_login(base, sleep_fn=fast_sleep, no_browser=True)

    # 2. Poll pending -> authenticated via session_token (with browser opener raising exception)
    respx.post(f"{base}/_auth/start").mock(
        return_value=httpx.Response(
            200,
            json={
                "flow_id": "f1",
                "user_code": "ABCD-1234",
                "verification_uri": "https://microsoft.com/devicelogin",
            },
        )
    )
    poll_route = respx.get(f"{base}/_auth/poll").mock(
        side_effect=[
            httpx.Response(200, json={"status": "pending"}),
            httpx.Response(
                200,
                json={
                    "status": "authenticated",
                    "session_token": "tok-from-json",
                    "email": "drosen@innodata.com",
                    "name": "Danny",
                    "tid": "tid-1",
                    "expires_at": 8888888888,
                },
            ),
        ]
    )

    def bad_browser(url: str) -> None:
        opened_urls.append(url)
        raise OSError("no display")

    sess = await run_device_code_login(
        base,
        sleep_fn=fast_sleep,
        no_browser=False,
        browser_opener=bad_browser,
        output_fn=printed.append,
    )
    assert sess.session_token == "tok-from-json"
    assert opened_urls == ["https://microsoft.com/devicelogin"]
    assert poll_route.call_count == 2

    # 3. Authenticated via Set-Cookie fallback (when session_token omitted from JSON)
    respx.get(f"{base}/_auth/poll").mock(
        return_value=httpx.Response(
            200,
            json={"status": "authenticated", "email": "drosen@innodata.com"},
            headers={"Set-Cookie": f"{SESSION_COOKIE_NAME}=tok-from-cookie; Path=/"},
        )
    )
    async with httpx.AsyncClient() as custom_http:
        sess_cookie = await run_device_code_login(
            base,
            http_client=custom_http,
            sleep_fn=fast_sleep,
            no_browser=True,
            output_fn=printed.append,
        )
    assert sess_cookie.session_token == "tok-from-cookie"

    # 4. Authenticated but neither session_token nor cookie present
    respx.get(f"{base}/_auth/poll").mock(
        return_value=httpx.Response(200, json={"status": "authenticated"})
    )
    with pytest.raises(RuntimeError, match="Gateway did not return a session_token"):
        await run_device_code_login(base, sleep_fn=fast_sleep, no_browser=True)

    # 5. Poll HTTP 400 error
    respx.get(f"{base}/_auth/poll").mock(return_value=httpx.Response(400, text="expired flow"))
    with pytest.raises(RuntimeError, match="Device login failed"):
        await run_device_code_login(base, sleep_fn=fast_sleep, no_browser=True)

    # 6. Poll status == error
    respx.get(f"{base}/_auth/poll").mock(
        return_value=httpx.Response(200, json={"status": "error", "error": "wrong tenant"})
    )
    with pytest.raises(RuntimeError, match="Authentication error: wrong tenant"):
        await run_device_code_login(base, sleep_fn=fast_sleep, no_browser=True)

    # 7. Poll timeout when always pending
    respx.get(f"{base}/_auth/poll").mock(
        return_value=httpx.Response(200, json={"status": "pending"})
    )
    with pytest.raises(TimeoutError, match="Timed out waiting"):
        await run_device_code_login(base, max_polls=2, sleep_fn=fast_sleep, no_browser=True)


@respx.mock
def test_cli_main_subcommands(tmp_path: Path) -> None:
    base = "https://code.innodata.studio"
    auth_file = tmp_path / "auth.json"
    foci_file = tmp_path / "foci.json"
    foci_file.write_text(json.dumps({"RefreshToken": {"k": {"secret": "rt-good"}}}))

    # 1. login via FOCI refresh token
    respx.post(f"{base}/_auth/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "authenticated",
                "session_token": "sess-foci",
                "email": "drosen@innodata.com",
                "name": "Danny",
                "tid": "tid-1",
                "expires_at": 9999999999,
            },
        )
    )
    rc_login = main(
        [
            "login",
            "--zoekt-url",
            base,
            "--auth-file",
            str(auth_file),
            "--foci-cache",
            str(foci_file),
        ]
    )
    assert rc_login == 0
    assert auth_file.is_file()

    # 2. login fallback to device code when FOCI exchange fails
    respx.post(f"{base}/_auth/token").mock(return_value=httpx.Response(401, text="expired rt"))
    respx.post(f"{base}/_auth/start").mock(
        return_value=httpx.Response(
            200,
            json={"flow_id": "f2", "user_code": "WXYZ", "verification_uri": "https://ms.com"},
        )
    )
    respx.get(f"{base}/_auth/poll").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "authenticated",
                "session_token": "sess-dev",
                "email": "drosen@innodata.com",
                "expires_at": 9999999999,
            },
        )
    )

    async def fast_sleep(_: float) -> None:
        return None

    rc_login_fallback = main(
        [
            "login",
            "--zoekt-url",
            base,
            "--auth-file",
            str(auth_file),
            "--foci-cache",
            str(foci_file),
            "--no-browser",
        ],
        sleep_fn=fast_sleep,
    )
    assert rc_login_fallback == 0

    # 3. login without FOCI cache
    missing_foci = tmp_path / "no-foci.json"
    rc_login_nofoci = main(
        [
            "login",
            "--zoekt-url",
            base,
            "--auth-file",
            str(auth_file),
            "--foci-cache",
            str(missing_foci),
            "--no-browser",
        ],
        sleep_fn=fast_sleep,
    )
    assert rc_login_nofoci == 0

    # 4. status (authenticated & unauthenticated)
    respx.get(f"{base}/_auth/whoami").mock(
        return_value=httpx.Response(
            200, json={"authenticated": True, "email": "drosen@innodata.com"}
        )
    )
    assert main(["status", "--zoekt-url", base, "--auth-file", str(auth_file)]) == 0

    respx.get(f"{base}/_auth/whoami").mock(return_value=httpx.Response(401, text="unauth"))
    assert main(["status", "--zoekt-url", base, "--auth-file", str(auth_file)]) == 1

    # 5. logout
    assert main(["logout", "--auth-file", str(auth_file)]) == 0
    assert not auth_file.exists()

    # 6. serve (default subcommand)
    served: list[Any] = []
    assert main(["--zoekt-url", base], server_runner=served.append) == 0
    assert len(served) == 1
    await_close = served[0].__dict__["_zoekt_client"].aclose()
    import asyncio as _asyncio

    _asyncio.run(await_close)


@respx.mock
async def test_search_posts_expected_payload_and_unwraps_result(client: ZoektClient) -> None:
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "Result": {
                    "Files": [{"fileName": "app.py", "matches": []}],
                    "Stats": {"FileCount": 1},
                }
            },
        )
    )

    result = await client.search(
        "lang:python def hello",
        num_context_lines=5,
        extra_opts={"Whole": True},
    )

    assert route.called
    sent = route.calls.last.request
    assert sent.url == SEARCH_URL
    body = sent.content.decode()
    assert '"Q":"lang:python def hello"' in body
    assert '"NumContextLines":5' in body
    assert '"ChunkMatches":true' in body
    assert '"Whole":true' in body
    assert result == {
        "Files": [{"fileName": "app.py", "matches": []}],
        "Stats": {"FileCount": 1},
    }


@respx.mock
async def test_client_with_injected_http_client_and_auth_headers() -> None:
    route = respx.post(LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={"List": {"Repos": [{"Repository": {"Name": "flask-app"}}]}},
        )
    )
    async with httpx.AsyncClient() as raw_http:
        async with ZoektClient(
            DEFAULT_BACKEND_URL,
            http_client=raw_http,
            token_provider=lambda: "dynamic-tok-99",
        ) as c:
            result = await c.list_repos()
            assert result == {"Repos": [{"Repository": {"Name": "flask-app"}}]}
            sent = route.calls.last.request
            assert sent.headers["Authorization"] == "Bearer dynamic-tok-99"
            assert f"{SESSION_COOKIE_NAME}=dynamic-tok-99" in sent.headers["Cookie"]


@respx.mock
async def test_search_and_post_error_branches(client: ZoektClient) -> None:
    # 500 error
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ZoektBackendError) as exc_info:
        await client.search("foo")
    assert exc_info.value.status_code == 500
    assert exc_info.value.body == "boom"

    # 401 error
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(401, text="unauth"))
    with pytest.raises(ZoektBackendError, match="run 'zoekt-mcp login'") as exc_401:
        await client.search("foo")
    assert exc_401.value.status_code == 401

    # Transport HTTPError
    respx.post(SEARCH_URL).mock(side_effect=httpx.ConnectError("conn refused"))
    with pytest.raises(ZoektBackendError, match="POST /api/search failed"):
        await client.search("foo")

    # Invalid JSON
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, text="not json at all"))
    with pytest.raises(ZoektBackendError, match="invalid JSON"):
        await client.search("foo")

    # Non-dict JSON
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(ZoektBackendError, match="non-object JSON"):
        await client.search("foo")


@respx.mock
async def test_get_file_returns_raw_text_and_handles_errors(client: ZoektClient) -> None:
    raw_source = 'def hello():\n    return "world"\n'
    route = respx.get(PRINT_URL).mock(
        return_value=httpx.Response(
            200,
            text=raw_source,
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )

    content = await client.get_file("flask-app", "app.py", branch="main")
    assert content == raw_source
    assert route.called

    # 404 error
    respx.get(PRINT_URL).mock(return_value=httpx.Response(404, text="not found"))
    with pytest.raises(ZoektBackendError) as exc_404:
        await client.get_file("flask-app", "missing.py")
    assert exc_404.value.status_code == 404

    # 401 error
    respx.get(PRINT_URL).mock(return_value=httpx.Response(401, text="unauth"))
    with pytest.raises(ZoektBackendError, match="run 'zoekt-mcp login'") as exc_401:
        await client.get_file("flask-app", "app.py")
    assert exc_401.value.status_code == 401

    # 200 text/html login page masquerading as source code on a .py file
    respx.get(PRINT_URL).mock(
        return_value=httpx.Response(
            200,
            text="<!DOCTYPE html><html>Sign in</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    with pytest.raises(ZoektBackendError, match="returned text/html instead of raw source"):
        await client.get_file("flask-app", "app.py")

    # Legitimate .html file is allowed when content-type is text/html
    html_src = await client.get_file("flask-app", "index.html")
    assert "<!DOCTYPE html>" in html_src

    # Transport error on GET /print
    respx.get(PRINT_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ZoektBackendError, match="GET /print failed"):
        await client.get_file("flask-app", "app.py")


@respx.mock
async def test_get_repo_metadata_handles_all_response_shapes(client: ZoektClient) -> None:
    meta_url = f"{DEFAULT_BACKEND_URL}/api/repo-metadata"

    # 1. Valid gateway payload wrapped under "repos" (plus non-dict entry skipped)
    respx.get(meta_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "generated_at": "2026-11-27T00:00:00Z",
                "repos": {
                    "InnodataAgenticResearch/makor": {
                        "owners": [{"login": "dannyzen-innodata", "contributions": 42}],
                        "pushed_at": "2026-11-27T00:00:00Z",
                        "pr_open": 1,
                        "pr_total": 10,
                    },
                    "invalid_entry": "not-a-dict",
                },
            },
        )
    )
    meta = await client.get_repo_metadata()
    assert list(meta.keys()) == ["InnodataAgenticResearch/makor"]
    assert meta["InnodataAgenticResearch/makor"]["pr_total"] == 10

    # 2. Bare zoekt-webserver returning 404
    respx.get(meta_url).mock(return_value=httpx.Response(404, text="not found"))
    assert await client.get_repo_metadata() == {}

    # 3. Invalid JSON on 200
    respx.get(meta_url).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    assert await client.get_repo_metadata() == {}

    # 4. Top-level JSON list instead of object
    respx.get(meta_url).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    assert await client.get_repo_metadata() == {}

    # 5. "repos" key is not a dict
    respx.get(meta_url).mock(return_value=httpx.Response(200, json={"repos": ["bad"]}))
    assert await client.get_repo_metadata() == {}

    # 6. Transport error
    respx.get(meta_url).mock(side_effect=httpx.ConnectError("unreachable"))
    assert await client.get_repo_metadata() == {}
