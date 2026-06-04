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

### Step 6 — Connect Claude Desktop to the Docker MCP gateway

Either:

- In Docker Desktop: open **MCP Toolkit > Clients** and connect Claude Desktop, **or**
- From the CLI:
  ```bash
  docker mcp client connect claude-desktop
  ```

Claude Desktop's `claude_desktop_config.json` will reference the gateway only —
it does **not** contain `ISE_PASSWORD` or any other secret.

Quit and reopen Claude Desktop after connecting.

### Step 7 — Verify

```bash
docker mcp server list     # should show ise-readonly enabled
docker mcp tools list      # should list the get_* tools
```

In Claude Desktop, the tools menu should now include the ISE tools.

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
