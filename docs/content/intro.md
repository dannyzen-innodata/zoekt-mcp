---
title: "zoekt-mcp"
sidebar_position: 1
slug: /
---

`zoekt-mcp` is a Model Context Protocol (MCP) server that exposes [Sourcegraph Zoekt](https://github.com/sourcegraph/zoekt) trigram and universal-ctags symbol search to AI agents (`Hermes Agent`, `Claude Code`, `Cursor`) with built-in Microsoft 365 (Entra ID) web gateway authentication.

## Key Capabilities

1. **M365 Web Gateway Authentication (`zoekt-mcp login`, `status`, `logout`)**:
   - Engineers authenticate once against our Microsoft 365-gated Zoekt web gateway (`https://code.innodata.studio`, routed via Cloudflare Tunnel and Makor Traefik ingress) using either zero-prompt FOCI refresh token exchange or an interactive Microsoft 365 device-code login.
   - Persists a 7-day HMAC-SHA256 signed session token in `~/.config/zoekt-mcp/auth.json` (`0600`) and automatically injects `Authorization: Bearer <token>` and `Cookie: zoekt_m365_session=<token>` on every MCP request.
2. **Three High-Signal MCP Tools**:
   - `list_repos`: Enumerate indexed repositories matching an optional regex filter, automatically enriched with GitHub `owners`, `pushed_at`, `pr_open`, and `pr_total` when connected to a gateway serving `/api/repo-metadata`.
   - `search_code`: Execute Zoekt trigram, regex, file, language, and `sym:` ctags symbol queries with decoded context lines and byte/column ranges.
   - `get_file`: Fetch raw source files via `/print?format=raw`, with strict detection against HTML SSO login pages masquerading as source code.
3. **100% Deterministic Engineering Standards**:
   - Enforced via `just check` (`nix flake check`): `ruff format`, `ruff check`, `mypy --strict`, `shac check`, `pytest -p randomly --cov=zoekt_mcp --cov-branch --cov-fail-under=100` (run twice in random order), Trivy security scanning, and this strict Docusaurus 3 build.

## Next Steps

- [M365 Gateway Authentication](./m365-authentication.md)
- [MCP Tools & Query Syntax](./mcp-tools.md)
- [Running the docs server](./docs-server.md)
