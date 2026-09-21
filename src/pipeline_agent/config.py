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
DEFAULT_MCP_TOOL_SURFACE = "generic"


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
            tenant=e.get("AGENTSWITCH_TENANT", "suryodaya"),
            model_name=e.get("MODEL_NAME", ""),
            output_dir=e.get("OUTPUT_DIR", "runs"),
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
