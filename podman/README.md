# ISE Read-Only MCP Server — Podman Setup

A read-only Model Context Protocol server for Cisco Identity Services Engine
(ISE) 3.1+, using the ISE OpenAPI only. Every tool is an HTTP GET; no writes
are possible.

> New here? Start with the [repo overview](../README.md). Prefer **Docker**?
> The Docker MCP Toolkit sibling lives in [`../docker/`](../docker/README.md);
> the `server.py` is byte-for-byte identical — only the runtime tooling differs.

> Not affiliated with or endorsed by Cisco. Use at your own risk.

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

## Providing secrets

The server reads its configuration from **environment variables** (`ISE_HOST`,
`ISE_USERNAME`, `ISE_PASSWORD`, and the optional `ISE_VERIFY_SSL` /
`ISE_MAX_RESPONSE_BYTES`). How those variables reach the container is your
choice, but one rule always applies: **never inline secrets as
`-e ISE_PASSWORD=…` arguments in your MCP client config file** — that file is
plaintext and often cloud-synced. Both methods below keep the credential out of
it.

- **Recommended — Podman secrets:** the credential lives in Podman's managed
  secret store, never in a file on disk you have to remember to lock down.
- **Simplest — env file:** convenient for local testing, but the value sits in
  a plaintext dotfile.

### Recommended: Podman secrets (step by step)

Podman has its own secret store. Unlike the Docker MCP Toolkit, Podman does not
auto-inject secrets as environment variables — you inject each one explicitly at
`podman run` time with `type=env`. This is the closest equivalent to the
Docker backend's managed-secret flow.

**Step 1 — Create a secret for each sensitive value.** Pipe the value in on
stdin (the trailing `-`) so it never lands in your shell history:

```bash
printf '%s' 'your-ise-password'  | podman secret create ise_password -
printf '%s' 'apiuser'            | podman secret create ise_username -
printf '%s' 'ise-ppan.corp.local'| podman secret create ise_host -
```

Store as many or as few as you like — at minimum keep `ISE_PASSWORD` in a
secret. Non-sensitive values (`ISE_VERIFY_SSL`, `ISE_MAX_RESPONSE_BYTES`) can
stay as plain `-e` flags.

**Step 2 — Verify they're stored** (values are never displayed):

```bash
podman secret list
```

**Step 3 — Run, injecting each secret as an env var.** The
`type=env,target=NAME` part is required — without it, Podman mounts the secret
as a *file* under `/run/secrets/`, which this server does not read:

```bash
podman run --rm -i \
  --secret ise_host,type=env,target=ISE_HOST \
  --secret ise_username,type=env,target=ISE_USERNAME \
  --secret ise_password,type=env,target=ISE_PASSWORD \
  -e ISE_VERIFY_SSL=yes \
  ise-readonly-mcp:latest
```

### Simplest: env file

Put the values in `podman/.env` and pass it with `--env-file`. The secret stays
out of the client config, but note it lives in a **plaintext file on disk** —
keep `podman/.env` `chmod 600` and gitignored (it already is):

```bash
podman run --rm -i --env-file podman/.env ise-readonly-mcp:latest
```

> Using Docker instead of Podman? The `docker/` backend integrates with the
> **Docker MCP Toolkit**, which manages secrets for you via
> `docker mcp secret set`. See `docker/README.md`.

---

## Claude Desktop integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent path on your OS. Create the Podman secrets first
(see [Providing secrets](#recommended-podman-secrets-step-by-step)), then point
the client at the secret-injecting invocation — no credential ends up in this
file:

```json
{
  "mcpServers": {
    "ise-readonly": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "--secret", "ise_host,type=env,target=ISE_HOST",
        "--secret", "ise_username,type=env,target=ISE_USERNAME",
        "--secret", "ise_password,type=env,target=ISE_PASSWORD",
        "-e", "ISE_VERIFY_SSL=yes",
        "ise-readonly-mcp:latest"
      ]
    }
  }
}
```

Prefer the env-file route for a quick local test? Swap the `--secret`/`-e`
arguments for `"--env-file", "/absolute/path/to/podman/.env"`.

## Claude Code integration

Using Podman secrets (recommended):

```bash
claude mcp add ise-readonly \
  podman run --rm -i \
    --secret ise_host,type=env,target=ISE_HOST \
    --secret ise_username,type=env,target=ISE_USERNAME \
    --secret ise_password,type=env,target=ISE_PASSWORD \
    -e ISE_VERIFY_SSL=yes \
    ise-readonly-mcp:latest
```

Or with an env file:

```bash
claude mcp add ise-readonly \
  podman run --rm -i --env-file /absolute/path/to/podman/.env ise-readonly-mcp:latest
```

Three scopes are available: omit `-s` for local (default — `~/.claude.json` under this project's entry), `-s project` to share via `.mcp.json`, or `-s user` for global use across all projects.

## Codex integration

OpenAI Codex reads MCP server config from a TOML file instead of JSON. Two scopes:

| Scope | File | Trust requirement |
|---|---|---|
| **global** | `~/.codex/config.toml` | none |
| **project** | `.codex/config.toml` at the project root | Codex only loads project files for **trusted** projects |

The translation from the Claude Desktop JSON above is mechanical: `mcpServers.foo` → `[mcp_servers.foo]`; same `command`, same `args`.

Using Podman secrets (recommended):

```toml
[mcp_servers.ise-readonly]
command = "podman"
args = [
  "run", "--rm", "-i",
  "--secret", "ise_host,type=env,target=ISE_HOST",
  "--secret", "ise_username,type=env,target=ISE_USERNAME",
  "--secret", "ise_password,type=env,target=ISE_PASSWORD",
  "-e", "ISE_VERIFY_SSL=yes",
  "ise-readonly-mcp:latest",
]
```

Or with an env file:

```toml
[mcp_servers.ise-readonly]
command = "podman"
args = [
  "run", "--rm", "-i",
  "--env-file", "/absolute/path/to/podman/.env",
  "ise-readonly-mcp:latest",
]
```

Restart Codex or open a new project thread so the MCP server loads.

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
