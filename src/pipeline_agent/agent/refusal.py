"""Formalizes the Track B fallback (capstone_plan.md Phase 3).

This is the mechanism the mandatory refusal task runs through, not the test
itself - the human-authored verifier (Phase 5) is a separate piece that reads
the persisted run artifact and the live database to confirm two things this
module is responsible for making true:

  1. `RefusalResult.message` states plainly that the result can't be
     determined with current permissions and names the missing domain -
     no hedging, no partial guess dressed up as an answer.
  2. Nothing upstream of this call ever attempts a write once a
     PermissionDeniedError has been seen for a required entity.
"""
from __future__ import annotations

from pipeline_agent.agent.contract import RefusalResult
from pipeline_agent.mcp.client import PermissionDeniedError


def build_refusal(exc: PermissionDeniedError, *, required_entities: list[str],
                   tools_attempted: list[str]) -> RefusalResult:
    message = (
        f"Cannot answer: this request needs {' and '.join(required_entities)}, which "
        f"live in the '{exc.domain}' domain. Seat 07's current policy does not include "
        f"'{exc.domain}', so {exc.entity} is not readable from this seat. This is a "
        f"policy/charter mismatch to escalate (the seat's charter requires this domain, "
        f"its policy excludes it), not something this agent can work around - no partial "
        f"or substitute answer follows, and no record was written."
    )
    return RefusalResult(
        missing_domain=exc.domain,
        missing_entities=required_entities,
        tools_attempted=tools_attempted,
        message=message,
    )
