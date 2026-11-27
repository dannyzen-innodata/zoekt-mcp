---
title: "M365 Gateway Authentication"
sidebar_position: 2
---

When Zoekt is exposed behind the Innodata Microsoft 365 FOCI Auth Gate (`https://code.innodata.studio`), engineers authenticate once before their AI agents connect over stdio.

## 1. Authenticate (`zoekt-mcp login`)

```bash
zoekt-mcp login --zoekt-url https://code.innodata.studio
```

How it works:

1. **Zero-Prompt Fast Path (FOCI Cache):** If you already have an active Microsoft 365 FOCI refresh token on your workstation or Makor VM (`~/.hermes/foci_token_cache.json` or `--foci-cache <path>`), `zoekt-mcp login` exchanges it directly at `POST /_auth/token` and mints a 7-day session token without opening a browser.
2. **Interactive Device-Code Flow:** Otherwise, `zoekt-mcp login` calls `POST /_auth/start`, displays a one-time code, opens `https://microsoft.com/devicelogin`, and polls `GET /_auth/poll` until your `@innodata.com` login completes.
3. **Token Persistence:** The signed session token is written to `~/.config/zoekt-mcp/auth.json` with `0600` permissions.

## 2. Check Session Status (`zoekt-mcp status`)

```bash
zoekt-mcp status --zoekt-url https://code.innodata.studio
```

Verifies your local token against `GET /_auth/whoami` on the gateway and prints your authenticated `@innodata.com` email, tenant ID (`be92c534-7b60-4630-bc01-ec5122548d47`), and expiration timestamp.

## 3. Configure Your MCP Client

### Hermes Agent (`~/.hermes/config.yaml`)

```yaml
mcp_servers:
  zoekt:
    command: zoekt-mcp
    args:
      - --zoekt-url
      - https://code.innodata.studio
```

### Claude Code / Cursor

```bash
claude mcp add zoekt \
  --env ZOEKT_URL=https://code.innodata.studio \
  -- uvx --from git+https://github.com/dannyzen-innodata/zoekt-mcp zoekt-mcp
```

### Nix Profile (Makor Workstations)

```bash
nix profile install github:dannyzen-innodata/zoekt-mcp#zoekt-mcp
```

## 4. Log Out (`zoekt-mcp logout`)

```bash
zoekt-mcp logout
```
