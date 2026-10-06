"""Requests the agent refuses before touching the book - keystone_enhancement_items.md §6.

The platform currently *permits* some of these (a quote can be created
already approved, G2; tax follows a client-supplied line amount, G1; another
user's agent task is writable). The agent must not lean on those gaps, and
some requests cannot be answered truthfully from this seat's data at all
(revenue attainment, K4; invoices, out of catalogue). Each rule says why, in
words a sales user can act on, and names the safe alternative.

Deterministic on purpose: the refusal task is graded on saying no correctly,
and a keyword rule is auditable where a model's judgement is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline_agent.agent.contract import RefusalResult


@dataclass(frozen=True)
class GuardRule:
    code: str
    pattern: re.Pattern
    entities: tuple[str, ...]
    message: str


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.IGNORECASE)


RULES: tuple[GuardRule, ...] = (
    GuardRule(
        "approve_quote",
        _rx(r"\bapprove\b.*\b(quote|quotation|order|qtn|so)\b",
            r"\b(quote|quotation|order|qtn|so)\b.*\bapprove(d)?\b",
            r"\b(mark|set|make|flag)\b.*\bapproved\b"),
        ("Quotation",),
        "Cannot approve a quote or order: approval belongs to an approver (admin or "
        "finance_admin), not to this seat, even though the platform currently accepts "
        "'approved' on create (report G2). I can submit it for approval instead."),
    GuardRule(
        "set_line_amount",
        _rx(r"\b(set|override|change|force)\b.*\b(line )?amount\b",
            r"\bamount\b.*\bon the (quote|line|order)\b",
            r"\b(set|override|change)\b.*\b(tax|taxes)\b.*\b(quote|order)\b"),
        ("Quotation", "SalesOrder"),
        "Cannot set line amounts or tax by hand: the platform computes tax from a "
        "client-supplied amount before correcting it (report G1), so a typed amount can "
        "mis-tax the customer. I will send quantity and rate and let the server price it."),
    GuardRule(
        "revenue_attainment",
        _rx(r"\b(attainment|against (the )?(goal|target|quota))\b",
            r"\b(revenue|sales)\b.*\b(vs\.?|versus)\b.*\b(goal|target|quota)\b",
            r"\bhow (close|far) .*\b(goal|target|quota)\b"),
        ("Goal",),
        "Cannot give revenue attainment against goal: the company-level goals read 0 "
        "against their targets because the platform does not roll them up (report K4), "
        "so any percentage would be wrong. I can list won deal value per rep instead."),
    GuardRule(
        "invoice_payment",
        # Plurals and "unpaid" added 2026-10-06: "unpaid invoices" matched
        # nothing, and only the platform's catalogue refusal stopped it.
        _rx(r"\b(invoices?|invoiced|un ?paid|paid|payments?|receivables?|overdue payments?)\b"),
        ("Invoice",),
        "Cannot answer invoice or payment questions: Invoice is in the accounting app, "
        "which is not in this seat's catalogue. Ask the AR / accounting seat or a finance "
        "user."),
    GuardRule(
        "delete_record",
        _rx(r"\b(delete|remove|purge|wipe)\b.*\b(deal|activity|party|lead|customer|quote)"),
        ("Deal",),
        "Cannot delete records: this seat has no delete tool and the book is shared with "
        "other teams. I can propose closing a duplicate deal as lost, with the reason, for "
        "you to confirm."),
    GuardRule(
        "other_users_task",
        _rx(r"\b(scheduled|weekly|daily)\b.*\b(task|job|agent)\b.*\b(prompt|edit|change|update|pause|run)",
            r"\b(edit|change|update)\b.*\b(agent ?task|prompt)\b"),
        ("AgentTask",),
        "Cannot change another user's scheduled agent task: it is not this seat's record, "
        "even though the platform currently shows it as writable. Ask its owner."),
    GuardRule(
        "bulk_close",
        _rx(r"\b(close|mark)\b.*\b(all|every|bulk)\b.*\b(lost|won|closed)\b",
            r"\b(all|every)\b.*\bdeals?\b.*\b(as )?(lost|closed)\b"),
        ("Deal",),
        "Will not close deals in bulk: closing is terminal and cannot be undone. I can list "
        "the close-lost candidates with reasons, and close each one only on your explicit "
        "confirmation."),
)


# A negation that directly governs action verbs: "do not create, update or
# delete", "never close", "without deleting". Only a chain of action verbs may
# sit between the negation and the last verb, so "don't hesitate to delete the
# deal" or "don't ask, just close all deals as lost" still reach the rules.
# Measured 2026-10-05: "Read-only: do not create, update or delete anything"
# was refused as delete_record, so stating a request is read-only got it
# refused.
_ACTION_VERB = (r"(?:creat|updat|delet|remov|purg|wip|clos|mark|approv|edit|chang|modif"
                r"|set|overrid|forc|writ|add)\w*")
_NEGATED_ACTIONS = re.compile(
    r"\b(?:do\s+not|don'?t|never|must\s+not|mustn'?t|should\s+not|shouldn'?t|without)\s+"
    rf"{_ACTION_VERB}(?:\s*(?:,|\bor\b|\band\b|,\s*(?:or|and)\b)\s*{_ACTION_VERB})*\b",
    re.IGNORECASE)


def check_request(text: str) -> RefusalResult | None:
    """A RefusalResult when the request matches a rule, else None."""
    text = _NEGATED_ACTIONS.sub(" ", text or "")
    for rule in RULES:
        if rule.pattern.search(text):
            return RefusalResult(missing_domain="", missing_entities=list(rule.entities),
                                 tools_attempted=[], message=f"[{rule.code}] {rule.message}")
    return None
