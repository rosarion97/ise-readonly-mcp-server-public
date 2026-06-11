# ISE Read-Only MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that
lets Claude **query — and only query** — a **Cisco Identity Services Engine
(ISE)** 3.1+ deployment over stdio, using the ISE **OpenAPI** only. Every tool
is an HTTP `GET`; no configuration changes are ever made. The ERS, MnT, and
pxGrid interfaces are intentionally not used.

> Not affiliated with or endorsed by Cisco. Use at your own risk.
> **Read-only is not the same as harmless** — policy sets, certificates, and
> deployment topology can contain sensitive operational data; scope the API
> admin account accordingly.

## Container backends

This repository ships a containerised build for both Docker and Podman. They
share a byte-for-byte identical `server.py`; only the runtime tooling differs.

| | When to use | Setup guide |
|---|---|---|
| 🐳 **Docker** | You use Docker Desktop + the MCP Toolkit | [`docker/README.md`](docker/README.md) |
| 🦭 **Podman** | You want rootless containers / no Docker Desktop | [`podman/README.md`](podman/README.md) |

Each guide is self-contained (build → secrets → register with Claude
Desktop / Claude Code → verify).

## Hard read-only guarantee

Every tool maps to an HTTP `GET` against the ISE OpenAPI
(`https://<pan>/api/v1/...`). The server contains no code path that issues
`POST`, `PUT`, `PATCH`, or `DELETE`, so it is incapable of modifying ISE
configuration regardless of what an LLM or user asks for. A dedicated
`attempt_write_operation` tool sits in the catalog as model-visible proof of
the contract; it performs no I/O and always refuses.

## Coverage at a glance

38 read tools across the OpenAPI surface:

* Deployment — nodes, node groups, PAN HA, cluster node status
* Network Access policy — policy sets, authentication/authorization rules,
  conditions, dictionaries, global and authorization exceptions
* Device Admin (TACACS+) policy — policy sets, rules, conditions, exceptions
* TrustSec — virtual networks, VN/VLAN and VN/SGT mappings, SG mappings,
  NBAR apps
* Certificates — system certificates, trusted certificates, CSRs
* Operations — profiler config, repositories (and files), patches, hotpatches

## Repository layout

```
.
├── README.md      # you are here — overview + backend chooser
├── docker/        # Docker variant + custom-catalog.yaml (Docker MCP Toolkit)
└── podman/        # Podman variant, rootless
```

`docker/server.py` and `podman/server.py` are kept byte-identical
(`diff -q docker/server.py podman/server.py`).

## Configuration

All configuration is via environment variables (container secrets or an
`--env-file`):

| Variable | Required | Purpose |
|----------|----------|---------|
| `ISE_HOST` | yes | Hostname/IP of the Primary Administration Node (PAN); no scheme. Pins this server to one deployment. |
| `ISE_USERNAME` | yes | ISE admin account with API access (read-only role recommended). |
| `ISE_PASSWORD` | yes | Password for `ISE_USERNAME`. |
| `ISE_VERIFY_SSL` | no | TLS verification; default `yes`. `no` only for self-signed lab certs. |
| `ISE_MAX_RESPONSE_BYTES` | no | JSON byte cap per tool response. Default `120000` (~30k tokens). |

See `docker/.env.example` for the template. Use a dedicated read-only admin
account — never a superadmin — and treat the password like any other secret.

## License

Provided as-is for internal use. Not affiliated with or endorsed by Cisco.
Pull requests that add additional **read-only** ISE OpenAPI endpoints are
welcome; any change that introduces a write-capable verb will be rejected.
