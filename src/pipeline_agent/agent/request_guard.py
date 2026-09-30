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
        _rx(r"\b(invoice|invoiced|paid|payment|receivable|overdue payment)\b"),
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


# Questions no data this seat can read will answer (crm_gap_fillup.md B8).
# Unlike RULES these are not unsafe - they are unanswerable, and the honest
# reply is to say which data is missing and who holds it, not to estimate.
DATA_LIMIT_RULES: tuple[GuardRule, ...] = (
    GuardRule(
        "stage_duration",
        _rx(r"\b(how long|how many days|since when)\b.*\b(in|at|entered)\s+"
            r"(the |its |their |this |that |each |current |a )*(stage|qualification|proposal|"
            r"negotiation)\b",
            r"\b(time|days|weeks) (spent )?in (the |its |their |each |current )*stage\b"),
        ("Deal",),
        "Cannot say how long a deal has been in its stage: the platform stores no stage-entry "
        "date or stage history. The agent's own run snapshots can bound it (the deal entered "
        "its stage no later than the first snapshot that saw it there), and a canonical run "
        "reports that bound in each row's rot_score - but it is a bound, not an answer."),
    GuardRule(
        "email_meeting_recency",
        _rx(r"\b(last|latest|recent|most recent)\b.*\b(e-?mails?|e-?mailed|meetings?|"
            r"calendar)\b",
            r"\bwhen did we (e-?mail|meet)\b"),
        ("EmailMessage", "CalendarEvent"),
        "Cannot answer email or meeting history: EmailMessage and CalendarEvent are in the "
        "email and scheduling apps, which are not in this seat's catalogue. Ask the Inbox seat "
        "(10) or the Calendar seat (19). I can report the last call or contact logged in CRM."),
    GuardRule(
        "support_tickets",
        _rx(r"\b(support )?tickets?\b", r"\bhelp ?desk\b"),
        ("Ticket",),
        "Cannot answer support-ticket questions: Ticket is in the support app, which is not in "
        "this seat's catalogue. Ask the Helpdesk seat (15)."),
    GuardRule(
        "contract_renewal",
        _rx(r"\bcontracts?\b.*\b(renew|renewal|expir\w*|end date|ends)\b",
            r"\brenewal dates?\b"),
        ("Contract",),
        "Cannot answer contract or renewal questions: Contract is in the contracts app, which "
        "is not in this seat's catalogue. Ask the Contract seat (17)."),
)


def check_request(text: str) -> RefusalResult | None:
    """A RefusalResult when the request matches a rule, else None."""
    for rule in RULES + DATA_LIMIT_RULES:
        if rule.pattern.search(text or ""):
            return RefusalResult(missing_domain="", missing_entities=list(rule.entities),
                                 tools_attempted=[], message=f"[{rule.code}] {rule.message}")
    return None
