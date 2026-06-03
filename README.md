# ISE Read-Only MCP Server — Podman Setup

A read-only Model Context Protocol server for Cisco Identity Services Engine
(ISE) 3.1+, using the ISE OpenAPI only. Every tool is an HTTP GET; no writes
are possible.

---

## Prerequisites

1. **Podman** installed and in `$PATH`.
2. An ISE Primary Administration Node (PAN) reachable from your machine.
3. An ISE admin account (or dedicated API user) with the **Super Admin**
   or **ERS Admin** + **OpenAPI** role.
4. **Open API enabled** on the ISE node:
   `Administration > System > Settings > API Settings > Open API` → toggle ON.
   Without this, every tool call returns 403.

---

## Quick start

```bash
# 1. Clone / enter the repo
cd ise-readonly-mcp-server

# 2. Create your env file
cp podman/.env.example podman/.env
$EDITOR podman/.env          # fill in ISE_HOST, ISE_USERNAME, ISE_PASSWORD

# 3. Build the image
podman build -t ise-readonly-mcp:latest podman/

# 4. Smoke-test (Ctrl-C to exit — expects a JSON-RPC ping on stdin)
podman run --rm --env-file podman/.env ise-readonly-mcp:latest
```

---

## Claude Desktop integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent path on your OS:

```json
{
  "mcpServers": {
    "ise-readonly": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/podman/.env",
        "ise-readonly-mcp:latest"
      ]
    }
  }
}
```

## Claude Code integration

```bash
claude mcp add ise-readonly \
  podman run --rm -i --env-file /absolute/path/to/podman/.env ise-readonly-mcp:latest
```

---

## Self-signed certificates

ISE PANs commonly ship with self-signed certificates. If your ISE uses one:

```
ISE_VERIFY_SSL=no
```

This suppresses urllib3 InsecureRequestWarning. For production, install the
ISE self-signed CA into your OS/container trust store and keep
`ISE_VERIFY_SSL=yes`.

---

## Rebuild after code changes

```bash
podman build --no-cache -t ise-readonly-mcp:latest podman/
```

---

## Omitted capabilities (ERS-only)

The following ISE domains are **not** available because they only exist under
the ERS API (`/ers/config/…`), which this server intentionally excludes:

| Domain | ERS path |
|--------|----------|
| Network devices & groups | `/ers/config/networkdevice/` |
| Internal users | `/ers/config/internaluser/` |
| Endpoint identities | `/ers/config/endpoint/` |
| Identity groups | `/ers/config/identitygroup/` |
| Security groups (SGTs) | `/ers/config/sgt/` |
| SGACLs | `/ers/config/sgacl/` |
| TrustSec egress matrix | `/ers/config/egressmatrixcell/` |

If you need those, a separate ERS MCP server would be required.
