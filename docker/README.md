# ISE Read-Only MCP Server — Docker (MCP Toolkit)

A Model Context Protocol (MCP) server that lets Claude query a **Cisco Identity
Services Engine (ISE)** 3.1+ deployment in **read-only mode** using the ISE
**OpenAPI** only. Every tool is an HTTP `GET`; no configuration changes are ever
made. The ERS, MnT, and pxGrid interfaces are intentionally not used.

This backend runs inside a Docker container managed by the **Docker MCP
Toolkit**. Your ISE credentials stay in Docker-managed secrets — they are never
written into Claude Desktop's configuration file.

> Looking for the plain-`docker run` or rootless version? Use the `podman/`
> backend in the parent directory. The two `server.py` files are identical;
> only the container file and integration method differ.

> Not affiliated with or endorsed by Cisco. Use at your own risk.

---

## Prerequisites

- **Docker Desktop** with the [MCP Toolkit](https://docs.docker.com/desktop/features/mcp/)
  feature installed and enabled.
- A **Cisco ISE 3.1+** deployment reachable from the Docker host, with the
  **Open API** enabled:
  `Administration > System > Settings > API Settings > Open API` → toggle ON.
  Without this, every tool call returns 403.
- An ISE admin account (or dedicated API user) with a role that grants OpenAPI
  read access (**Super Admin** or **ERS Admin** + **OpenAPI**). Scope it as
  narrowly as your environment allows.

---

## Providing secrets

The server reads its configuration from **environment variables** (`ISE_HOST`,
`ISE_USERNAME`, `ISE_PASSWORD`, and the optional `ISE_VERIFY_SSL` /
`ISE_MAX_RESPONSE_BYTES`).

For this backend the intended path is the **Docker MCP Toolkit secret store**:
store values with `docker mcp secret set …`, and the gateway injects them as env
vars at launch. This achieves both halves of the design goal:

1. The secret value never appears in Claude Desktop's config file (which is
   plaintext and often cloud-synced).
2. The value lives in Docker's managed secret store, not a plaintext file on
   disk.

The step-by-step setup below uses this path. **Do not** inline secrets as
`-e ISE_PASSWORD=…` arguments in `claude_desktop_config.json` — that puts the
credential straight into the config file and defeats the entire design.

> **About `.env` files:** a `docker run --env-file docker/.env …` invocation
> keeps secrets out of the client config, but the value still sits in a
> plaintext dotfile and you lose the managed gateway integration. Treat
> `.env` as a **local build/smoke-test convenience only** (e.g. the Step 2
> sanity check), not as the way you wire this server into a client. For an
> env-file-first workflow, use the rootless `podman/` backend instead, which
> is designed around it.

---

## Step-by-Step Setup

### Step 1 — Get the project files

Clone or download this repository. The Docker backend lives in `docker/`:

- `Dockerfile`
- `.dockerignore`
- `server.py`
- `requirements.txt`
- `custom-catalog.yaml`
- `.env.example` (reference only — not used by the Toolkit; secrets live in Docker)

### Step 2 — Build the Docker image

From the repository root:

```bash
docker build -t ise-readonly-mcp-server:latest docker/
```

The image tag must match the `image:` field in `custom-catalog.yaml`
(`ise-readonly-mcp-server:latest`).

### Step 3 — Store secrets in Docker (not in Claude Desktop)

```bash
docker mcp secret set ISE_HOST="ise-ppan.corp.local"
docker mcp secret set ISE_USERNAME="apiuser"
docker mcp secret set ISE_PASSWORD="<password>"
docker mcp secret set ISE_VERIFY_SSL="yes"
```

Use `ISE_VERIFY_SSL="yes"` whenever you can. Only set it to `"no"` if ISE uses a
self-signed certificate and you accept the risk; the safer alternative is to
mount ISE's CA cert into the container and keep verification on. The optional
`ISE_MAX_RESPONSE_BYTES` secret can be set the same way (default 120000).

Verify the secrets are stored (values are not displayed):

```bash
docker mcp secret list
```

### Step 4 — Install the custom catalog

```bash
mkdir -p ~/.docker/mcp/catalogs
cp docker/custom-catalog.yaml ~/.docker/mcp/catalogs/custom.yaml
```

If you already maintain a `custom.yaml` with other servers, merge the
`ise-readonly` entry under its `registry:` key instead of overwriting.

### Step 5 — Enable the server in the registry

`~/.docker/mcp/registry.yaml` lists which servers from your catalogs are active.
The file has a single top-level `registry:` key. Add the `ise-readonly` entry
under it — **do not overwrite the file** if it already exists.

Final shape of the file:

```yaml
registry:
  ise-readonly:
    catalog: custom
    enabled: true
  # ... any other servers you already had stay here
```

If `registry.yaml` does not exist yet, create it with exactly the snippet above.

### Step 6 — Point Claude Desktop at the Docker MCP gateway

Add the gateway block to Claude Desktop's config (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`). The gateway runs as a container, mounts the Docker socket so it can spawn the `ise-readonly-mcp-server` container on demand, mounts your `~/.docker/mcp` directory so it can read the catalog and registry, and mounts the Docker secrets-engine socket so it can resolve the `ISE_*` secrets you set in Step 3.

Replace `<your-username>` with your macOS username (run `whoami` to check):

```json
{
  "mcpServers": {
    "mcp-toolkit-gateway": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", "/Users/<your-username>/.docker/mcp:/mcp",
        "-v", "/Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock",
        "docker/mcp-gateway:latest",
        "--catalog=/mcp/catalogs/custom.yaml",
        "--registry=/mcp/registry.yaml",
        "--transport=stdio"
      ]
    }
  }
}
```

All three bind-mounts are required:

1. **`/var/run/docker.sock`** — lets the gateway spawn the `ise-readonly-mcp-server` container.
2. **`~/.docker/mcp`** — the gateway reads the catalog and registry from here.
3. **`docker-secrets-engine/engine.sock`** — the resolver socket Docker Desktop exposes for the secret store. Without it the gateway resolves your secret URLs to empty strings and `docker run -e ""` rejects the env flags, so the server never starts and only the gateway's internal admin tools show up. On Linux Docker Desktop the host path is `~/.docker/desktop/secrets-engine/engine.sock` instead; check with `find ~ -name engine.sock 2>/dev/null`.

Quit and reopen Claude Desktop. `claude_desktop_config.json` never contains `ISE_PASSWORD` — the gateway resolves it from Docker's secret store at request time.

> **Shortcut alternative.** `docker mcp client connect claude-desktop` (or **MCP Toolkit > Clients** in Docker Desktop) will write a similar block for you automatically. The explicit JSON above gives you control over which catalogs load and survives Docker Desktop updates that may rewrite the auto-managed entry.

### Step 7 — Verify

```bash
docker mcp server list     # should show ise-readonly enabled
docker mcp tools list      # should list the get_* tools
```

In Claude Desktop, the tools menu should now include the ISE tools.

---

## Using with Claude Code

Claude Code uses the same `mcp-toolkit-gateway` block from Step 6 — same `command`, same `args` — but reads it from a different file. There are three scopes:

| Scope | File | Sharing |
|---|---|---|
| **local** (default) | `~/.claude.json`, under this project's entry | just you, just this project |
| **project** | `.mcp.json` at the project root | shared via git with collaborators |
| **user** (global) | `~/.claude.json`, top level | just you, every project |

**Easiest path — let the CLI write it for you.** Replace `<your-username>` and pick the scope you want:

```bash
claude mcp add -s user mcp-toolkit-gateway -- \
  docker run -i --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /Users/<your-username>/.docker/mcp:/mcp \
  -v /Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock \
  docker/mcp-gateway:latest \
  --catalog=/mcp/catalogs/custom.yaml \
  --registry=/mcp/registry.yaml \
  --transport=stdio
```

Use `-s user` for global, `-s project` to commit the entry to `.mcp.json` for collaborators, or omit `-s` for the default local scope. Everything after `--` is the same docker invocation Claude Desktop uses — the schema is byte-for-byte identical.

Verify with `claude mcp list`. The Step 3 secrets and Step 4 / Step 5 catalog and registry setup all carry over; nothing else changes.

---

## Using with Codex

OpenAI Codex reads MCP server config from a TOML file instead of JSON. Two scopes:

| Scope | File | Trust requirement |
|---|---|---|
| **global** | `~/.codex/config.toml` | none |
| **project** | `.codex/config.toml` at the project root | Codex only loads project files for **trusted** projects — confirm trust in Codex before relying on this scope |

Same gateway invocation as Step 6, mechanically translated from JSON to TOML (`mcpServers.foo` → `[mcp_servers.foo]`; same `command`, same `args`). Replace `<your-username>` with your macOS username (run `whoami` to check):

```toml
[mcp_servers.mcp-toolkit-gateway]
command = "docker"
args = [
  "run",
  "-i",
  "--rm",
  "-v",
  "/var/run/docker.sock:/var/run/docker.sock",
  "-v",
  "/Users/<your-username>/.docker/mcp:/mcp",
  "-v",
  "/Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock",
  "docker/mcp-gateway:latest",
  "--catalog=/mcp/catalogs/custom.yaml",
  "--registry=/mcp/registry.yaml",
  "--transport=stdio",
]
```

Restart Codex or open a new project thread so the MCP server loads. The Step 3 secrets and Step 4 / Step 5 catalog and registry setup all carry over; nothing else changes.

---

## Usage Examples

Once connected, try these natural-language prompts in Claude:

- **"List all nodes in the ISE deployment and their personas"**
- **"Show me the network access policy sets"**
- **"What authorization rules are in policy set <uuid>?"**
- **"List the TrustSec virtual networks"**
- **"Show the trusted certificate inventory"**
- **"What repositories are configured on ISE?"**
- **"List the patches installed across the deployment"**
- **"Show the profiler configuration"**

---

## Self-signed certificates

ISE PANs commonly ship with self-signed certificates. If yours does:

```bash
docker mcp secret set ISE_VERIFY_SSL="no"
```

This suppresses the urllib3 InsecureRequestWarning. For production, install the
ISE CA into the container trust store and keep `ISE_VERIFY_SSL="yes"`.

---

## Rebuild after code changes

```bash
docker build --no-cache -t ise-readonly-mcp-server:latest docker/
```

Restart Claude Desktop after rebuilding.

---

## Architecture

```
Claude Desktop  ←→  Docker MCP Gateway  ←→  ise-readonly container  ←→  HTTPS  ←→  ISE OpenAPI (/api/v1)
                                              │
                                              └─ reads ISE_HOST / ISE_USERNAME / ISE_PASSWORD /
                                                 ISE_VERIFY_SSL from Docker-managed secrets at startup
```

Claude Desktop connects to the Docker MCP gateway. The gateway launches the
`ise-readonly-mcp-server` container, injecting your stored secrets as
environment variables. The container speaks JSON-RPC over stdio with the
gateway, and HTTPS to the ISE OpenAPI at `https://<ISE_HOST>/api/v1/…`. Secrets
never appear in Claude Desktop's config file. The container runs as a non-root
user (uid 1001).

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

---

## License

Provided as-is for integrating Cisco ISE with Claude via MCP. Use at your own
risk. Not affiliated with or endorsed by Cisco.
