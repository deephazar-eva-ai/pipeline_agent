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
    needed = " and ".join(required_entities)
    tail = ("not something this agent can work around - no partial or substitute answer "
            "follows, and no record was written.")
    if exc.cites_domain:
        message = (
            f"Cannot answer: this request needs {needed}, which live in the "
            f"'{exc.domain}' domain, and the platform refused on those grounds. Seat 07's "
            f"current policy does not include '{exc.domain}', so {exc.entity} is not "
            f"readable from this seat. This is a policy/charter mismatch to escalate, "
            + tail
        )
    else:
        # The measured case. Say what was actually refused - a tool that is not
        # in this seat's catalogue - rather than inventing a domain story the
        # platform never told us. `missing_domain` is reported as empty so a
        # verifier cannot be fooled into confirming a domain exclusion that was
        # never observed.
        message = (
            f"Cannot answer: this request needs {needed}, and the platform refused the "
            f"call on {exc.entity} because the tool is not in this seat's catalogue "
            f"(tools/list). No domain was named by the platform, so none is claimed here. "
            f"Escalate as a seat-capability gap, " + tail
        )
    return RefusalResult(
        missing_domain=exc.domain if exc.cites_domain else "",
        missing_entities=required_entities,
        tools_attempted=tools_attempted,
        message=message,
    )
