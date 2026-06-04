"""
Cisco ISE Read-Only MCP Server
================================
A production-grade Model Context Protocol (MCP) server that exposes
READ-ONLY access to the Cisco Identity Services Engine (ISE) OpenAPI
over stdio transport.

HARD CONSTRAINTS
----------------
* Every tool maps to an HTTP GET against https://<ISE_PPAN_HOST>/api/v1/...
  (ISE OpenAPI). No ERS, MnT, or pxGrid paths exist anywhere.
* No POST, PUT, PATCH, or DELETE is ever issued. Attempts return a clear
  refusal string.
* Credentials (ISE_USERNAME / ISE_PASSWORD) are read from env at startup.
  They are never logged and never returned in any tool response.
* Transport is stdio. The container is invoked directly by an MCP client
  (Claude Desktop / Claude Code).

OMISSIONS (ERS-only, out of scope)
------------------------------------
* Network devices and network device groups  (ERS: config/networkdevice)
* Internal users, identity groups            (ERS: config/internaluser)
* Endpoint identities and endpoint groups    (ERS: config/endpoint)
* Security groups (SGTs), SGACLs             (ERS: config/sgt)
* TrustSec egress matrix                     (ERS: config/egressmatrixcell)
These capabilities only exist under the ERS API and cannot be served here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging — all output goes to stderr so stdout stays clean for JSON-RPC
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ise-readonly")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def _handle_sigterm(*_args: Any) -> None:
    logger.info("Received SIGTERM — shutting down")
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)

# ---------------------------------------------------------------------------
# Configuration — fail-fast at startup if required vars are missing
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30  # seconds

ISE_HOST = os.environ.get("ISE_HOST", "").strip()
if not ISE_HOST:
    print(
        "ERROR: ISE_HOST environment variable is not set. "
        "Set it to the hostname or IP of the ISE Primary PAN (no scheme).",
        file=sys.stderr,
    )
    sys.exit(1)

ISE_USERNAME = os.environ.get("ISE_USERNAME", "").strip()
if not ISE_USERNAME:
    print(
        "ERROR: ISE_USERNAME environment variable is not set. "
        "Set it to the ISE admin / API user name.",
        file=sys.stderr,
    )
    sys.exit(1)

ISE_PASSWORD = os.environ.get("ISE_PASSWORD", "").strip()
if not ISE_PASSWORD:
    print(
        "ERROR: ISE_PASSWORD environment variable is not set. "
        "Pass it with `--env-file .env` when running the container.",
        file=sys.stderr,
    )
    sys.exit(1)

_verify_raw = os.environ.get("ISE_VERIFY_SSL", "yes").strip().lower()
ISE_VERIFY_SSL: bool | str = _verify_raw != "no"
if not ISE_VERIFY_SSL:
    import urllib3  # noqa: PLC0415

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logger.info("SSL verification DISABLED (ISE_VERIFY_SSL=no) — InsecureRequestWarning suppressed")

MAX_RESPONSE_BYTES = max(
    int(os.environ.get("ISE_MAX_RESPONSE_BYTES", "120000")),
    10_000,
)

BASE_URL = f"https://{ISE_HOST}/api"

# Hard ceiling for any tool's per-page size parameter.
PER_PAGE_CAP = 100

# ---------------------------------------------------------------------------
# Shared requests.Session — Basic auth + JSON headers
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.auth = (ISE_USERNAME, ISE_PASSWORD)
_session.headers.update(
    {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ise-readonly-mcp/1.0 (stdio)",
    }
)
_session.verify = ISE_VERIFY_SSL

mcp = FastMCP("ise-readonly")

# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# ISE hostnames: letters, digits, dot, hyphen — covers FQDN and short names.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,253}$")
# ISE object names: letters, digits, underscore, hyphen, dot, space (max 128).
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\ \-]{1,128}$")


def _validate_uuid(value: str, label: str = "id") -> str:
    v = value.strip()
    if not _UUID_RE.match(v):
        raise ValueError(f"{label} must be a valid UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx), got: {v!r}")
    return v


def _validate_hostname(value: str, label: str = "hostname") -> str:
    v = value.strip()
    if not v or not _HOSTNAME_RE.match(v):
        raise ValueError(f"{label} must be a valid hostname or IP, got: {v!r}")
    return v


def _validate_name(value: str, label: str = "name") -> str:
    v = value.strip()
    if not _NAME_RE.match(v):
        raise ValueError(
            f"{label} contains invalid characters or exceeds 128 chars. "
            "Allowed: letters, digits, underscore, hyphen, dot, space."
        )
    return v


# ---------------------------------------------------------------------------
# HTTP helpers — READ-ONLY
# ---------------------------------------------------------------------------

READ_ONLY_REFUSAL = "This server is read-only. Operation refused."


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Issue a GET to the ISE OpenAPI and return parsed JSON.

    Raises ValueError with a descriptive (but credential-free) message on
    any non-200 response.
    """
    url = f"{BASE_URL}{path}"
    try:
        response = _session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise ValueError(f"Network error contacting ISE API: {exc}") from None

    status = response.status_code
    if status == 200:
        try:
            return response.json()
        except ValueError:
            raise ValueError("ISE API returned a non-JSON 200 response.") from None
    if status == 401:
        raise ValueError(
            "401 Unauthorized: ISE rejected the credentials. "
            "Check ISE_USERNAME / ISE_PASSWORD and ensure the account has the "
            "required admin role."
        )
    if status == 403:
        raise ValueError(
            "403 Forbidden: the API user lacks permission for this resource, "
            "or Open API is not enabled on this ISE node. "
            "Enable it at: Administration > System > Settings > API Settings > Open API."
        )
    if status == 404:
        raise ValueError(
            f"404 Not found: {path}. "
            "Verify the resource exists and that Open API is enabled at: "
            "Administration > System > Settings > API Settings > Open API."
        )
    if status == 429:
        raise ValueError("429 Too Many Requests: ISE rate limit hit. Retry after a moment.")
    raise ValueError(f"ISE API error {status} for {path}: {response.text[:200]}")


def _refuse_write(verb: str, path: str) -> str:
    return f"{READ_ONLY_REFUSAL} (attempted {verb.upper()} {path})"


# ---------------------------------------------------------------------------
# Response-size guardrails
# ---------------------------------------------------------------------------


def _clamp_per_page(size: int, cap: int = PER_PAGE_CAP) -> int:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return cap
    if n < 1:
        return 1
    return min(n, cap)


def _bounded(
    payload: Any,
    hint: str = (
        "Narrow the query: lower size, advance page, or filter by id/name."
    ),
) -> Any:
    """Cap the JSON-serialised size of a tool response.

    If the payload fits MAX_RESPONSE_BYTES, return it unchanged.
    Otherwise return a truncation envelope:
    * For lists: include as many leading items as fit and report counts.
    * For dicts: return a string preview so the caller sees the schema.
    The envelope's ``_hint`` tells the model how to re-query.
    """
    try:
        raw = json.dumps(payload, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return payload

    if len(raw) <= MAX_RESPONSE_BYTES:
        return payload

    if isinstance(payload, list):
        kept: list[Any] = []
        running = 0
        for item in payload:
            chunk = len(json.dumps(item, separators=(",", ":"), default=str))
            if running + chunk > MAX_RESPONSE_BYTES:
                break
            kept.append(item)
            running += chunk
        return {
            "_truncated": True,
            "_returned": len(kept),
            "_total": len(payload),
            "_bytes_cap": MAX_RESPONSE_BYTES,
            "_hint": hint,
            "data": kept,
        }

    return {
        "_truncated": True,
        "_original_bytes": len(raw),
        "_bytes_cap": MAX_RESPONSE_BYTES,
        "_hint": hint,
        "preview": raw[:MAX_RESPONSE_BYTES],
    }


def _project(items: Any, keep: set[str], verbose: bool) -> Any:
    """Narrow a list of dicts to the ``keep`` fields unless verbose=True."""
    if verbose or not isinstance(items, list):
        return items
    return [
        {k: v for k, v in item.items() if k in keep}
        for item in items
        if isinstance(item, dict)
    ]


# Field projections for high-cardinality endpoints.
_NODE_KEEP = {
    "hostname", "personaList", "nodeServiceTypes", "primaryPanNode",
    "nodeStatus", "fqdn", "ipAddress", "nodeType",
}
_POLICY_SET_KEEP = {
    "id", "name", "description", "isProxy", "state", "condition",
    "serviceName", "rank",
}
_AUTH_RULE_KEEP = {
    "id", "name", "state", "rank", "condition", "identitySource",
    "ifAuthFail", "ifUserNotFound", "ifProcessFail",
}
_AUTHZ_RULE_KEEP = {
    "id", "name", "state", "rank", "condition", "profile", "securityGroup",
}
_CONDITION_KEEP = {
    "id", "name", "description", "conditionType", "isNegate",
    "dictionaryName", "attributeName", "operator", "attributeValue",
}
_TRUSTED_CERT_KEEP = {
    "id", "friendlyName", "subject", "issuedTo", "issuedBy",
    "validFrom", "expirationDate", "status", "trustForIseAuth",
    "trustForClientAuth", "trustForCertificateBasedAdminAuth",
}
_CSR_KEEP = {
    "id", "friendlyName", "subject", "keyType", "keyLength",
    "digestType", "hostName", "timeStamp",
}
_REPO_KEEP = {
    "name", "protocol", "serverName", "path", "userName",
}
_VN_KEEP = {"id", "name", "description", "additionalAttributes"}
_SGM_KEEP = {"id", "name", "sgt", "deployTo", "deployType", "state"}


# ---------------------------------------------------------------------------
# Helper — unwrap ISE paged SearchResult envelope
# ---------------------------------------------------------------------------


def _unwrap(data: Any) -> Any:
    """ISE list endpoints return {"SearchResult": {"total": N, "resources": [...]}}."""
    if isinstance(data, dict):
        sr = data.get("SearchResult")
        if isinstance(sr, dict):
            resources = sr.get("resources", [])
            # Attach total as metadata so callers can surface it.
            if isinstance(resources, list):
                return {"total": sr.get("total", len(resources)), "resources": resources}
    return data


# ---------------------------------------------------------------------------
# DEPLOYMENT / NODE TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_deployment_nodes(verbose: bool = False) -> Any:
    """List all ISE nodes in the deployment. GET /api/v1/deployment/node

    Returns hostname, persona list, node type, IP address, and status by
    default. Pass verbose=True for the full node record.
    """
    data = _get("/v1/deployment/node")
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _NODE_KEEP, verbose)
    return _bounded(unwrapped, hint="Pass verbose=True for full node records, or call get_deployment_node(hostname) for a single node.")


@mcp.tool()
def get_deployment_node(hostname: str) -> Any:
    """Return details of a single ISE node by hostname. GET /api/v1/deployment/node/{hostname}"""
    h = _validate_hostname(hostname)
    return _get(f"/v1/deployment/node/{h}")


@mcp.tool()
def get_pan_ha() -> Any:
    """Return the Primary Administration Node high-availability configuration. GET /api/v1/deployment/pan-ha"""
    return _get("/v1/deployment/pan-ha")


@mcp.tool()
def get_node_groups() -> Any:
    """List all ISE node groups (used for PSN load-balancing). GET /api/v1/deployment/node-group"""
    data = _get("/v1/deployment/node-group")
    return _bounded(data, hint="Node groups are typically few; if truncated, call get_deployment_node per hostname.")


@mcp.tool()
def get_cluster_node_status() -> Any:
    """Return replication / sync status for every node in the ISE cluster. GET /api/v1/cluster/node/status"""
    return _get("/v1/cluster/node/status")


# ---------------------------------------------------------------------------
# NETWORK ACCESS POLICY TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_policy_sets(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List all Network Access policy sets. GET /api/v1/policy/network-access/policy-set

    Paged via page (1-based) and size (clamped to PER_PAGE_CAP=100, default 50).
    """
    size = _clamp_per_page(size)
    data = _get("/v1/policy/network-access/policy-set", params={"page": page, "size": size})
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _POLICY_SET_KEEP, verbose)
        return _bounded(resources, hint="Advance page or lower size; pass verbose=True for full policy set records.")
    return _bounded(data, hint="Advance page or lower size.")


@mcp.tool()
def get_policy_set(policy_id: str) -> Any:
    """Return a single Network Access policy set by UUID. GET /api/v1/policy/network-access/policy-set/{id}"""
    pid = _validate_uuid(policy_id, "policy_id")
    return _get(f"/v1/policy/network-access/policy-set/{pid}")


@mcp.tool()
def get_policy_set_authentication_rules(policy_id: str, verbose: bool = False) -> Any:
    """Return authentication rules for a Network Access policy set. GET /api/v1/policy/network-access/policy-set/{policyId}/authentication"""
    pid = _validate_uuid(policy_id, "policy_id")
    data = _get(f"/v1/policy/network-access/policy-set/{pid}/authentication")
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _AUTH_RULE_KEEP, verbose)
        return _bounded(resources, hint="Pass verbose=True for full rule records including condition trees.")
    return _bounded(data)


@mcp.tool()
def get_policy_set_authorization_rules(policy_id: str, verbose: bool = False) -> Any:
    """Return authorization rules for a Network Access policy set. GET /api/v1/policy/network-access/policy-set/{policyId}/authorization"""
    pid = _validate_uuid(policy_id, "policy_id")
    data = _get(f"/v1/policy/network-access/policy-set/{pid}/authorization")
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _AUTHZ_RULE_KEEP, verbose)
        return _bounded(resources, hint="Pass verbose=True for full authorization rule records.")
    return _bounded(data)


@mcp.tool()
def get_network_access_conditions(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List conditions in the Network Access Library. GET /api/v1/policy/network-access/condition"""
    size = _clamp_per_page(size)
    data = _get("/v1/policy/network-access/condition", params={"page": page, "size": size})
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _CONDITION_KEEP, verbose)
        return _bounded(resources, hint="Advance page or lower size; pass verbose=True for full condition records.")
    return _bounded(data)


@mcp.tool()
def get_network_access_condition(condition_id: str) -> Any:
    """Return a single Network Access Library condition by UUID. GET /api/v1/policy/network-access/condition/{id}"""
    cid = _validate_uuid(condition_id, "condition_id")
    return _get(f"/v1/policy/network-access/condition/{cid}")


@mcp.tool()
def get_network_access_dictionaries(page: int = 1, size: int = 50) -> Any:
    """List all Network Access attribute dictionaries. GET /api/v1/policy/network-access/dictionaries"""
    size = _clamp_per_page(size)
    data = _get("/v1/policy/network-access/dictionaries", params={"page": page, "size": size})
    return _bounded(data, hint="Advance page or lower size to page through dictionary list.")


@mcp.tool()
def get_network_access_dictionary(dictionary_name: str) -> Any:
    """Return a single Network Access dictionary and its attributes by name. GET /api/v1/policy/network-access/dictionaries/{name}"""
    name = _validate_name(dictionary_name, "dictionary_name")
    return _get(f"/v1/policy/network-access/dictionaries/{name}")


@mcp.tool()
def get_network_access_global_exceptions() -> Any:
    """Return the global exception rules for Network Access policy. GET /api/v1/policy/network-access/global-exception"""
    data = _get("/v1/policy/network-access/global-exception")
    return _bounded(data, hint="Global exceptions are shared across all policy sets.")


@mcp.tool()
def get_network_access_authorization_exceptions() -> Any:
    """Return global authorization exception rules for Network Access policy. GET /api/v1/policy/network-access/authorization-exception"""
    data = _get("/v1/policy/network-access/authorization-exception")
    return _bounded(data)


# ---------------------------------------------------------------------------
# DEVICE ADMIN POLICY TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_device_admin_policy_sets(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List all Device Administration (TACACS+) policy sets. GET /api/v1/policy/device-admin/policy-set"""
    size = _clamp_per_page(size)
    data = _get("/v1/policy/device-admin/policy-set", params={"page": page, "size": size})
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _POLICY_SET_KEEP, verbose)
        return _bounded(resources, hint="Advance page or lower size; pass verbose=True for full records.")
    return _bounded(data)


@mcp.tool()
def get_device_admin_policy_set(policy_id: str) -> Any:
    """Return a single Device Administration policy set by UUID. GET /api/v1/policy/device-admin/policy-set/{id}"""
    pid = _validate_uuid(policy_id, "policy_id")
    return _get(f"/v1/policy/device-admin/policy-set/{pid}")


@mcp.tool()
def get_device_admin_authentication_rules(policy_id: str, verbose: bool = False) -> Any:
    """Return authentication rules for a Device Administration policy set. GET /api/v1/policy/device-admin/policy-set/{policyId}/authentication"""
    pid = _validate_uuid(policy_id, "policy_id")
    data = _get(f"/v1/policy/device-admin/policy-set/{pid}/authentication")
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _AUTH_RULE_KEEP, verbose)
        return _bounded(resources, hint="Pass verbose=True for full rule records.")
    return _bounded(data)


@mcp.tool()
def get_device_admin_authorization_rules(policy_id: str, verbose: bool = False) -> Any:
    """Return authorization rules for a Device Administration policy set. GET /api/v1/policy/device-admin/policy-set/{policyId}/authorization"""
    pid = _validate_uuid(policy_id, "policy_id")
    data = _get(f"/v1/policy/device-admin/policy-set/{pid}/authorization")
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _AUTHZ_RULE_KEEP, verbose)
        return _bounded(resources, hint="Pass verbose=True for full authorization rule records.")
    return _bounded(data)


@mcp.tool()
def get_device_admin_conditions(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List conditions in the Device Administration Library. GET /api/v1/policy/device-admin/condition"""
    size = _clamp_per_page(size)
    data = _get("/v1/policy/device-admin/condition", params={"page": page, "size": size})
    resources = data if isinstance(data, list) else data.get("response", data)
    if isinstance(resources, list):
        resources = _project(resources, _CONDITION_KEEP, verbose)
        return _bounded(resources, hint="Advance page or lower size; pass verbose=True for full records.")
    return _bounded(data)


@mcp.tool()
def get_device_admin_global_exceptions() -> Any:
    """Return global exception rules for Device Administration policy. GET /api/v1/policy/device-admin/global-exception"""
    data = _get("/v1/policy/device-admin/global-exception")
    return _bounded(data)


# ---------------------------------------------------------------------------
# TRUSTSEC (OpenAPI) TOOLS
# ---------------------------------------------------------------------------
# NOTE: SGTs, SGACLs, and egress matrix cells are accessible only via the ERS
# API (ERS: config/sgt, config/egressmatrixcell) and are out of scope.
# The tools below cover only the TrustSec endpoints present in the OpenAPI.


@mcp.tool()
def get_trustsec_virtual_networks(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List TrustSec virtual networks (VNs). GET /api/v1/trustsec/virtualnetwork"""
    size = _clamp_per_page(size)
    data = _get("/v1/trustsec/virtualnetwork", params={"page": page, "size": size})
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _VN_KEEP, verbose)
    return _bounded(unwrapped, hint="Advance page or lower size to page through virtual networks.")


@mcp.tool()
def get_trustsec_vn_vlan_mappings(page: int = 1, size: int = 50) -> Any:
    """List TrustSec VN-to-VLAN mappings. GET /api/v1/trustsec/vn-vlan-mapping"""
    size = _clamp_per_page(size)
    data = _get("/v1/trustsec/vn-vlan-mapping", params={"page": page, "size": size})
    return _bounded(_unwrap(data), hint="Advance page or lower size.")


@mcp.tool()
def get_trustsec_vn_sgt_mappings(page: int = 1, size: int = 50) -> Any:
    """List TrustSec VN-to-SGT mappings. GET /api/v1/trustsec/vn-sgt-mapping"""
    size = _clamp_per_page(size)
    data = _get("/v1/trustsec/vn-sgt-mapping", params={"page": page, "size": size})
    return _bounded(_unwrap(data), hint="Advance page or lower size.")


@mcp.tool()
def get_trustsec_sg_mappings(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List TrustSec Security Group to IP/host mappings. GET /api/v1/trustsec/sg-mapping"""
    size = _clamp_per_page(size)
    data = _get("/v1/trustsec/sg-mapping", params={"page": page, "size": size})
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _SGM_KEEP, verbose)
    return _bounded(unwrapped, hint="Advance page or lower size; pass verbose=True for full records.")


@mcp.tool()
def get_trustsec_nbar_apps(page: int = 1, size: int = 50) -> Any:
    """List NBAR (network-based application recognition) apps visible to TrustSec SGACLs. GET /api/v1/trustsec/sgacl/nbarapp"""
    size = _clamp_per_page(size)
    data = _get("/v1/trustsec/sgacl/nbarapp", params={"page": page, "size": size})
    return _bounded(_unwrap(data), hint="Advance page or lower size.")


# ---------------------------------------------------------------------------
# CERTIFICATE TOOLS
# ---------------------------------------------------------------------------
# Private key material is never present in GET responses — ISE returns only
# certificate metadata (subject, issuer, validity, usage flags). No stripping
# of sensitive fields is required beyond what ISE already withholds.


@mcp.tool()
def get_system_certificates(hostname: str, page: int = 1, size: int = 50) -> Any:
    """List system (identity) certificates installed on an ISE node. GET /api/v1/certs/system-certificate/{hostName}

    hostname is the ISE node short name or FQDN (e.g. ise-ppan.corp.local).
    """
    h = _validate_hostname(hostname)
    size = _clamp_per_page(size)
    data = _get(f"/v1/certs/system-certificate/{h}", params={"page": page, "size": size})
    return _bounded(_unwrap(data), hint="Advance page or lower size; call with a different hostname for other nodes.")


@mcp.tool()
def get_system_certificate(hostname: str, cert_id: str) -> Any:
    """Return metadata for a single system certificate on an ISE node. GET /api/v1/certs/system-certificate/{hostName}/{id}"""
    h = _validate_hostname(hostname)
    cid = _validate_uuid(cert_id, "cert_id")
    return _get(f"/v1/certs/system-certificate/{h}/{cid}")


@mcp.tool()
def get_trusted_certificates(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List all trusted CA certificates in the ISE trust store. GET /api/v1/certs/trusted-certificate"""
    size = _clamp_per_page(size)
    data = _get("/v1/certs/trusted-certificate", params={"page": page, "size": size})
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _TRUSTED_CERT_KEEP, verbose)
    return _bounded(unwrapped, hint="Advance page or lower size; pass verbose=True for full cert metadata.")


@mcp.tool()
def get_trusted_certificate(cert_id: str) -> Any:
    """Return metadata for a single trusted CA certificate by UUID. GET /api/v1/certs/trusted-certificate/{id}"""
    cid = _validate_uuid(cert_id, "cert_id")
    return _get(f"/v1/certs/trusted-certificate/{cid}")


@mcp.tool()
def get_csrs(page: int = 1, size: int = 50, verbose: bool = False) -> Any:
    """List certificate signing requests (CSRs) across all ISE nodes. GET /api/v1/certs/certificate-signing-request"""
    size = _clamp_per_page(size)
    data = _get("/v1/certs/certificate-signing-request", params={"page": page, "size": size})
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _CSR_KEEP, verbose)
    return _bounded(unwrapped, hint="Advance page or lower size; CSRs contain no private key material.")


# ---------------------------------------------------------------------------
# PROFILER TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_profiler_config() -> Any:
    """Return the ISE profiler service configuration (probes enabled, CoA settings). GET /api/v1/profiler-config"""
    return _get("/v1/profiler-config")


# ---------------------------------------------------------------------------
# REPOSITORY TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_repositories(verbose: bool = False) -> Any:
    """List all configured ISE repositories (FTP, SFTP, NFS, etc.). GET /api/v1/repository

    Passwords / credentials stored with repositories are not returned by ISE.
    """
    data = _get("/v1/repository")
    unwrapped = _unwrap(data)
    if isinstance(unwrapped, dict) and "resources" in unwrapped:
        unwrapped["resources"] = _project(unwrapped["resources"], _REPO_KEEP, verbose)
    elif isinstance(unwrapped, list):
        unwrapped = _project(unwrapped, _REPO_KEEP, verbose)
    return _bounded(unwrapped, hint="Pass verbose=True for full repository records (no credentials returned).")


@mcp.tool()
def get_repository(repo_name: str) -> Any:
    """Return details of a single ISE repository by name. GET /api/v1/repository/{name}"""
    name = _validate_name(repo_name, "repo_name")
    return _get(f"/v1/repository/{name}")


@mcp.tool()
def get_repository_files(repo_name: str) -> Any:
    """List files available in an ISE repository. GET /api/v1/repository/{name}/files"""
    name = _validate_name(repo_name, "repo_name")
    data = _get(f"/v1/repository/{name}/files")
    return _bounded(data, hint="If the file list is truncated, the repository may require sub-directory navigation not exposed by this endpoint.")


# ---------------------------------------------------------------------------
# PATCH / HOTPATCH TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_patches() -> Any:
    """Return a list of patches installed on all ISE nodes. GET /api/v1/patch"""
    return _get("/v1/patch")


@mcp.tool()
def get_hotpatches() -> Any:
    """Return a list of hot patches installed on all ISE nodes. GET /api/v1/hotpatch"""
    return _get("/v1/hotpatch")


# ---------------------------------------------------------------------------
# EXPLICIT WRITE REFUSAL TOOL
# ---------------------------------------------------------------------------


@mcp.tool()
def attempt_write_operation(
    method: str = "POST", path: str = "/example"
) -> str:
    """Refuses any write attempt. This server is READ-ONLY by design.

    Use this tool to verify the server's read-only stance; it always
    returns the standard refusal string and performs no network I/O.
    """
    return _refuse_write(method, path)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "ISE Read-Only MCP Server starting — host=%s ssl_verify=%s max_bytes=%d",
        ISE_HOST,
        ISE_VERIFY_SSL,
        MAX_RESPONSE_BYTES,
    )
    mcp.run()
