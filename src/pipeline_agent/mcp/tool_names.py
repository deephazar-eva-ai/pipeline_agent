"""Tool-name translation between the two surfaces AgentSwitch exposes.

document/agent_tools.json  -> 13 generic tools; args carry {"entity": ...}
document/mcp_list.json     -> entity-scoped tools: "Deal.list", "Activity.create"

Which surface a caller gets is a property of the credential, not of this
code, so it is configuration (MCP_TOOL_SURFACE), not a hard-coded choice.
Every workflow function keeps calling the generic form; real_client does
the translation in one place.

Evidence: EAG_V3_capstone/document/agent_tools.json (13 tools, bare names,
entity passed as an argument) vs document/mcp_list.json (249 tools, every
name of the form Entity.action, no bare names, no entity argument).
"""
from __future__ import annotations

GENERIC = "generic"
ENTITY_SCOPED = "entity_scoped"

VALID_SURFACES = (GENERIC, ENTITY_SCOPED)

# Generic tool -> entity-scoped suffix, for the subset that has a direct
# counterpart in the captured catalogue.
_SUFFIX = {
    "get_schema": "schema",
    "list": "list",
    "get": "get",
    "create": "create",
    "update": "update",
    "delete": "delete",
    "count": "count",
    "search": "search",
}

# Generic tools with no entity-scoped counterpart in the captured
# catalogue, and therefore unusable when the surface is entity_scoped.
_NO_ENTITY_SCOPED_COUNTERPART = {
    "transition": "transitions are action-specific: use Deal.qualify, Deal.negotiate, ...",
    "report": "aggregate reports are served by the generic tool surface only",
    "make_from": "document chains are served by the generic tool surface only",
    "bulk_update": "bulk update is served by the generic tool surface only",
    "financial_report": "canonical reports are served by the generic tool surface only",
}


class SurfaceError(Exception):
    """The requested call cannot be expressed on the configured surface."""



def normalise_surface(value: str) -> str:
    v = (value or "").strip().lower().replace("-", "_")
    if v in VALID_SURFACES:
        return v
    raise SurfaceError(
        f"unknown MCP_TOOL_SURFACE {value!r}; expected one of {VALID_SURFACES}"
    )


def to_wire(surface: str, name: str, arguments: dict) -> tuple[str, dict]:
    """Map one generic call onto the configured surface.

    Returns (wire_tool_name, wire_arguments). The generic surface passes
    both through untouched; the entity-scoped surface moves the entity out
    of the arguments and into the tool name.
    """
    if surface == GENERIC:
        return name, dict(arguments)

    if surface != ENTITY_SCOPED:
        raise SurfaceError(f"unknown surface {surface!r}")

    if name in _NO_ENTITY_SCOPED_COUNTERPART:
        raise SurfaceError(
            f"tool {name!r} has no entity-scoped counterpart: "
            f"{_NO_ENTITY_SCOPED_COUNTERPART[name]}"
        )

    suffix = _SUFFIX.get(name)
    if suffix is None:
        raise SurfaceError(f"tool {name!r} is not part of the 13-tool surface")

    entity = arguments.get("entity")
    if not entity:
        raise SurfaceError(f"tool {name!r} needs an entity to name an entity-scoped tool")

    wire_arguments = {k: v for k, v in arguments.items() if k != "entity"}

    # The entity-scoped tools declare every filter and every writable field as
    # a TOP-LEVEL property, with "additionalProperties": false. The generic
    # surface wraps those in `filters` / `data` envelopes, so passing them
    # through unchanged is rejected with a bare "Invalid tool arguments".
    # Confirmed against the live inputSchema for Activity.list, Activity.create
    # and Activity.update on 2026-09-21.
    for envelope in ("filters", "data"):
        nested = wire_arguments.pop(envelope, None)
        if isinstance(nested, dict):
            wire_arguments.update(nested)

    return f"{entity}.{suffix}", wire_arguments


def from_wire(surface: str, wire_name: str) -> tuple[str, str]:
    """Reverse map: an entity-scoped name back to (entity, suffix).

    Used to report which tools a credential can actually see.
    """
    if surface == GENERIC:
        return "", wire_name
    if "." not in wire_name:
        raise SurfaceError(f"{wire_name!r} is not an entity-scoped tool name")
    entity, _, suffix = wire_name.partition(".")
    return entity, suffix
