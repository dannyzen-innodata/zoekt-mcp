---
title: "MCP Tools & Query Syntax"
sidebar_position: 3
---

`zoekt-mcp` registers three MCP tools on the stdio server:

## 1. `list_repos(filter="")`

Lists indexed repositories matching `filter` (e.g. `"makor"` or `"repo:InnodataAgenticResearch/.*"`). Returns `count` and a list of repository metadata objects (`name`, `url`, `source`, `branches`, `index_time`, `has_symbols`). When connected to a gateway exposing `GET /api/repo-metadata` (such as `https://code.innodata.studio`), each matching repository also includes `owners` (top human contributors and commit counts), `pushed_at`, `pr_open`, and `pr_total`.

## 2. `search_code(query, max_results=50, context_lines=3)`

Executes a Zoekt trigram/symbol query across all indexed repositories:

- `repo:NAME`: Filter by repository regex
- `file:PATH`: Filter by file path regex
- `lang:LANG`: Filter by language (`python`, `typescript`, `go`, `nix`)
- `sym:IDENT`: Match symbol definitions extracted by `universal-ctags`
- `case:yes`: Case-sensitive matching
- `or`: Boolean alternation (whitespace is boolean AND)

## 3. `get_file(repo, path, branch="HEAD")`

Fetches the raw file contents via `GET /print?format=raw`. If the gateway is unauthenticated or returns an HTML login page instead of raw text, `get_file` raises a structured `ZoektBackendError` instructing the user to run `zoekt-mcp login`.
