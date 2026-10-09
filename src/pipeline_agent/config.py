"""Settings, loaded from the environment and never echoed back whole.

One rule drives this file: secrets go in, secrets never come back out. Every
place that logs or persists a Settings object must go through `redacted()`,
not `dataclasses.asdict()` directly - the run artifact (harness/artifacts.py)
depends on that.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

_SECRET_FIELDS = ("mcp_token",)

# The platform snapshot contains two MCP catalogues.  The seat-specific
# catalogue exposes the 13 generic tools (where the entity travels in the
# arguments); the standard MCP catalogue exposes entity-scoped names such as
# ``Deal.list``.  This is deliberately a setting rather than a guess made by
# the workflow.
#
# The default is what the seat 07 credential actually serves, measured with
# ``--task preflight`` on 2026-09-21: 237 entity-scoped tools, no bare generic
# names at all. The earlier ``generic`` default came from the captured
# agent_tools.json and was simply wrong for this credential - every call was
# rejected with "This tool is not available to your seat".
DEFAULT_MCP_TOOL_SURFACE = "entity_scoped"

# Which business a URL serves. The tenant used to default to "suryodaya"
# whatever the URL, so Keystone runs were recorded as Suryodaya in their own
# artifacts (2026-09-28, run 20260928T091641Z). It is now derived from the URL,
# and an explicit AGENTSWITCH_TENANT that contradicts the URL is refused.
KNOWN_TENANT_HOSTS = {
    "agentswitch.theschoolofai.in": "suryodaya",
    "class.agentswitch.theschoolofai.in": "keystone",
}


def tenant_for_url(url: str) -> str:
    from urllib.parse import urlparse
    return KNOWN_TENANT_HOSTS.get((urlparse(url).hostname or "").lower(), "unknown")


@dataclass(frozen=True)
class Settings:
    mcp_url: str = ""
    mcp_token: str = ""
    mcp_tool_surface: str = DEFAULT_MCP_TOOL_SURFACE
    tenant: str = "suryodaya"
    model_name: str = ""
    output_dir: str = "runs"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = env if env is not None else os.environ
        return cls(
            mcp_url=e.get("AGENTSWITCH_MCP_URL", ""),
            mcp_token=e.get("AGENTSWITCH_MCP_TOKEN", ""),
            mcp_tool_surface=e.get("MCP_TOOL_SURFACE", DEFAULT_MCP_TOOL_SURFACE),
            tenant=(e.get("AGENTSWITCH_TENANT")
                    or tenant_for_url(e.get("AGENTSWITCH_MCP_URL", ""))),
            model_name=e.get("MODEL_NAME", ""),
            output_dir=e.get("OUTPUT_DIR", "runs"),
        )

    @classmethod
    def from_central_env(cls, env: dict | None = None) -> "Settings":
        """Settings for the central evaluator (agentswitch-harness.toml).

        The evaluator supplies its own variable names and a disposable copy of
        the instance whose host is not in KNOWN_TENANT_HOSTS, so the tenant
        comes from AGENTSWITCH_INSTANCE rather than from the URL. No .env is
        read on this path.
        """
        e = env if env is not None else os.environ
        base = e.get("AGENTSWITCH_BASE_URL", "").rstrip("/")
        if base.endswith("/api/mcp"):
            base = base[: -len("/api/mcp")]  # the transport appends it
        model = e.get("OPENAI_MODEL", "")
        return cls(
            mcp_url=base,
            mcp_token=e.get("AGENTSWITCH_TOKEN", ""),
            mcp_tool_surface=DEFAULT_MCP_TOOL_SURFACE,
            tenant=e.get("AGENTSWITCH_INSTANCE", "") or "unknown",
            model_name=f"openai:{model}" if model else "",
            output_dir="runs",
        )

    def redacted(self) -> dict:
        """Safe to log, print, or write into a run artifact."""
        out = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            out[k] = "***redacted***" if k in _SECRET_FIELDS and v else v
        return out

    def require_mcp_credentials(self) -> None:
        """Raise with a precise, actionable message instead of a bare KeyError.

        Called by RealMCPClient, never by the stub - the dry-run smoke test
        must work with zero credentials configured.
        """
        missing = [name for name, val in (("AGENTSWITCH_MCP_URL", self.mcp_url),
                                           ("AGENTSWITCH_MCP_TOKEN", self.mcp_token))
                   if not val]
        if missing:
            raise RuntimeError(
                "Missing MCP credentials: " + ", ".join(missing) +
                ". Copy .env.example to .env and fill these in, or run with "
                "--mode dry-run to exercise the harness against the stub client."
            )
        served = tenant_for_url(self.mcp_url)
        if served != "unknown" and self.tenant != served:
            raise RuntimeError(
                f"AGENTSWITCH_TENANT is {self.tenant!r} but {self.mcp_url} serves "
                f"{served!r}. Fix or unset AGENTSWITCH_TENANT; it is derived from the "
                f"URL when unset.")
