"""Unit tests for the response shaping helpers and MCP tools in :mod:`zoekt_mcp.server`."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import respx

from zoekt_mcp.client import DEFAULT_BACKEND_URL, ZoektClient
from zoekt_mcp.config import ZoektConfig
from zoekt_mcp.server import (
    _decode_line,
    _shape_chunk_match,
    _shape_list_result,
    _shape_search_result,
    build_server,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_decode_line_handles_base64() -> None:
    assert _decode_line(_b64("def hello():")) == "def hello():"


def test_decode_line_falls_back_on_non_base64() -> None:
    assert _decode_line("plain text") == "plain text"
    assert _decode_line(123) == "123"


def test_decode_line_handles_none_and_empty() -> None:
    assert _decode_line(None) == ""
    assert _decode_line("") == ""


def test_shape_search_result_matches_live_shape() -> None:
    raw = {
        "FileCount": 1,
        "MatchCount": 2,
        "Duration": 715529,
        "Files": [
            {
                "FileName": "app.py",
                "Repository": "flask-app",
                "Language": "Python",
                "Branches": ["main"],
                "Score": 85000000010,
                "LineMatches": [
                    {
                        "LineNumber": 20,
                        "Line": _b64("def hello():"),
                        "LineStart": 459,
                        "LineEnd": 472,
                        "LineFragments": [
                            {"LineOffset": 0, "MatchLength": 3},
                            {"LineOffset": 4, "MatchLength": 5},
                        ],
                        "Before": _b64("# context before\n"),
                        "After": None,
                    }
                ],
            }
        ],
    }

    shaped = _shape_search_result("def hello", raw)

    assert shaped["query"] == "def hello"
    assert shaped["file_count"] == 1
    assert shaped["match_count"] == 2
    assert shaped["duration_ns"] == 715529
    assert len(shaped["files"]) == 1

    f = shaped["files"][0]
    assert f["repo"] == "flask-app"
    assert f["file"] == "app.py"
    assert f["language"] == "Python"
    assert f["branches"] == ["main"]

    m = f["matches"][0]
    assert m["line"] == 20
    assert m["text"] == "def hello():"
    assert m["before"] == "# context before\n"
    assert m["after"] is None
    assert m["highlights"] == [
        {"line_offset": 0, "length": 3},
        {"line_offset": 4, "length": 5},
    ]


def test_shape_search_result_prefers_chunk_matches_with_symbols() -> None:
    raw = {
        "FileCount": 1,
        "MatchCount": 2,
        "Duration": 110200,
        "Files": [
            {
                "FileName": "index.js",
                "Repository": "express-app",
                "Language": "JavaScript",
                "Branches": ["main"],
                "Score": 7901,
                "ChunkMatches": [
                    {
                        "Content": _b64("const USERS = [\n  {id: 1},\n"),
                        "ContentStart": {"ByteOffset": 264, "LineNumber": 11, "Column": 1},
                        "Ranges": [
                            {
                                "Start": {"ByteOffset": 295, "LineNumber": 11, "Column": 7},
                                "End": {"ByteOffset": 300, "LineNumber": 11, "Column": 12},
                            }
                        ],
                        "SymbolInfo": [
                            {"Sym": "USERS", "Kind": "variable", "Parent": "", "ParentKind": ""}
                        ],
                        "BestLineMatch": 11,
                        "Score": 7901,
                    }
                ],
            }
        ],
    }

    shaped = _shape_search_result("sym:users", raw)
    file = shaped["files"][0]
    assert len(file["matches"]) == 1

    match = file["matches"][0]
    assert match["line"] == 11
    assert match["start_line"] == 11
    assert "const USERS" in match["text"]
    assert match["ranges"] == [{"start_line": 11, "start_col": 7, "end_line": 11, "end_col": 12}]
    assert match["symbols"] == [{"name": "USERS", "kind": "variable", "parent": None}]


def test_shape_chunk_match_survives_missing_subfields() -> None:
    shaped = _shape_chunk_match({})
    assert shaped["line"] is None
    assert shaped["text"] == ""
    assert shaped["ranges"] == []
    assert shaped["symbols"] is None


def test_shape_search_result_tolerates_missing_fields() -> None:
    shaped = _shape_search_result("q", {})
    assert shaped == {
        "query": "q",
        "file_count": None,
        "match_count": None,
        "duration_ns": None,
        "files": [],
    }


def test_shape_list_result_matches_live_shape() -> None:
    raw = {
        "Repos": [
            {
                "Repository": {
                    "Name": "flask-app",
                    "URL": "",
                    "Source": "/tmp/flask-app",
                    "Branches": [{"Name": "main", "Version": "abc123"}],
                    "HasSymbols": True,
                },
                "IndexMetadata": {"IndexTime": "2026-04-08T15:47:31Z"},
                "Stats": {"Documents": 3},
            },
            {
                "Repository": {
                    "Name": "express-app",
                    "URL": "",
                    "Source": "/tmp/express-app",
                    "Branches": [{"Name": "main", "Version": "def456"}],
                    "HasSymbols": True,
                },
                "IndexMetadata": {"IndexTime": "2026-04-08T15:47:31Z"},
                "Stats": {"Documents": 3},
            },
        ]
    }

    shaped = _shape_list_result(raw)

    assert shaped["count"] == 2
    names = [r["name"] for r in shaped["repos"]]
    assert names == ["flask-app", "express-app"]
    assert shaped["repos"][0]["branches"] == ["main"]
    assert shaped["repos"][0]["source"] == "/tmp/flask-app"
    assert shaped["repos"][0]["has_symbols"] is True
    assert shaped["repos"][0]["index_time"] == "2026-04-08T15:47:31Z"


def test_shape_list_result_empty() -> None:
    assert _shape_list_result({}) == {"count": 0, "repos": []}


def test_shape_list_result_enriches_gateway_metadata() -> None:
    raw = {
        "Repos": [
            {"Repository": {"Name": "github.com/InnodataAgenticResearch/makor"}},
            {"Repository": {"Name": "flask-app"}},
            {"Repository": {"Name": "unmatched-repo"}},
        ]
    }
    metadata = {
        "InnodataAgenticResearch/makor": {
            "owners": [{"login": "dannyzen-innodata", "contributions": 50}],
            "pushed_at": "2026-11-27T00:00:00Z",
            "pr_open": 2,
            "pr_total": 15,
        },
        "flask-app": {
            "owners": [{"login": "alice", "contributions": 5}],
            "pushed_at": "2026-11-20T00:00:00Z",
            "pr_open": 0,
            "pr_total": 1,
        },
    }
    shaped = _shape_list_result(raw, metadata=metadata)
    assert shaped["count"] == 3
    assert shaped["repos"][0]["owners"] == [{"login": "dannyzen-innodata", "contributions": 50}]
    assert shaped["repos"][0]["pushed_at"] == "2026-11-27T00:00:00Z"
    assert shaped["repos"][0]["pr_open"] == 2
    assert shaped["repos"][0]["pr_total"] == 15
    assert shaped["repos"][1]["owners"] == [{"login": "alice", "contributions": 5}]
    assert "owners" not in shaped["repos"][2]


@respx.mock
async def test_build_server_and_tool_execution(tmp_path: Path) -> None:
    respx.post(f"{DEFAULT_BACKEND_URL}/api/search").mock(
        return_value=httpx.Response(
            200,
            json={"Result": {"FileCount": 1, "MatchCount": 1, "Duration": 100, "Files": []}},
        )
    )
    respx.post(f"{DEFAULT_BACKEND_URL}/api/list").mock(
        return_value=httpx.Response(
            200,
            json={"List": {"Repos": [{"Repository": {"Name": "makor"}}]}},
        )
    )
    respx.get(f"{DEFAULT_BACKEND_URL}/api/repo-metadata").mock(
        return_value=httpx.Response(404, text="not found")
    )
    respx.get(f"{DEFAULT_BACKEND_URL}/print").mock(
        return_value=httpx.Response(
            200,
            text="# makor\n",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )

    cfg = ZoektConfig(
        backend_url=DEFAULT_BACKEND_URL,
        timeout=5.0,
        auth_token="tok-123",
        auth_file=tmp_path / "auth.json",
    )
    async with ZoektClient(DEFAULT_BACKEND_URL, auth_token="tok-123") as custom_client:
        mcp = build_server(cfg, client=custom_client)
        # Invoke the registered tools via FastMCP tool manager
        tools: dict[str, Any] = {t.name: t for t in mcp._tool_manager.list_tools()}
        assert set(tools.keys()) == {"search_code", "list_repos", "get_file"}

        search_res = await tools["search_code"].fn(
            query="def hello", max_results=10, context_lines=2
        )
        assert search_res["file_count"] == 1

        list_all = await tools["list_repos"].fn(filter="")
        assert list_all["count"] == 1

        list_bare = await tools["list_repos"].fn(filter="makor")
        assert list_bare["count"] == 1

        list_prefixed = await tools["list_repos"].fn(filter="repo:makor")
        assert list_prefixed["count"] == 1

        file_res = await tools["get_file"].fn(repo="makor", path="README.md", branch="HEAD")
        assert file_res["content"] == "# makor\n"

    # Also verify default client construction path in build_server
    mcp_default = build_server(cfg)
    await mcp_default.__dict__["_zoekt_client"].aclose()
