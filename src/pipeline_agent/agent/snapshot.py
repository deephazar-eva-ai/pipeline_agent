"""History without a backend: one snapshot per run, in the run artifact.

crm_gap_fillup.md B2. The gap report's `DealSnapshot` ledger (gapreport
§5.3, `deals_snapshot_schema.json`) needs a new platform table. Until it
exists, every canonical run writes `snapshot.json` next to `result.json`:
one row per in-scope deal, with the fields that table proposes plus
`captured_at`. Reading the earlier snapshots back gives what the platform
cannot:

* stage regression - a deal's stage is now earlier in its pipeline than in a
  previous snapshot;
* time in stage - observed when the stage changed between two snapshots,
  otherwise only bounded;
* close-date pushes - `expected_close_date` moved later;
* what changed since the last run.

History starts with the first snapshot. Nothing here pretends otherwise:
every derived value says which snapshots it rests on.

Snapshots are per tenant and never mix the stub with a live book: the
header carries the tenant and the mode, and only matching files are read.
Only runs whose `status.json` says `completed` count - a crashed run's
snapshot may be partial.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline_agent.agent.pipeline_checks import display_title

SNAPSHOT_FILE = "snapshot.json"

# DealFlow's forward order, used when a deal's pipeline cannot be read.
DEFAULT_STAGE_ORDER = ("new", "qualification", "proposal", "negotiation")

# Fields frozen into every row, as `deals_snapshot_schema.json` proposes.
ROW_FIELDS = ("title", "stage", "pipeline", "party_id", "owner", "value", "currency",
              "probability", "expected_close_date", "lost_reason", "created_at",
              "updated_at")

# How many earlier snapshots a run reads back. Enough for a quarter of daily
# runs; older history is still on disk for anyone who wants it.
HISTORY_LIMIT = 120


def _parse(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed


def _day(value: Any) -> str:
    day = str(value or "")[:10]
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return ""
    return day


@dataclass
class Snapshot:
    captured_at: str
    run_id: str
    rows: dict[str, dict]


@dataclass
class SnapshotHistory:
    """Earlier snapshots of this tenant, oldest first."""

    tenant: str
    snapshots: list[Snapshot] = field(default_factory=list)
    skipped: int = 0

    @property
    def latest(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def rows_for(self, deal_id: str) -> list[tuple[str, dict]]:
        return [(s.captured_at, s.rows[deal_id]) for s in self.snapshots if deal_id in s.rows]


def load_history(output_dir: str | Path, tenant: str, *,
                 limit: int = HISTORY_LIMIT) -> SnapshotHistory:
    """Every completed run's snapshot for this tenant, oldest first."""
    history = SnapshotHistory(tenant=tenant)
    found: list[Snapshot] = []
    for path in Path(output_dir).glob(f"*/{SNAPSHOT_FILE}"):
        try:
            status = json.loads((path.parent / "status.json").read_text()).get("status")
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            history.skipped += 1
            continue
        if status != "completed" or doc.get("tenant") != tenant:
            continue
        rows = {r.get("deal_id"): r for r in doc.get("rows") or [] if r.get("deal_id")}
        found.append(Snapshot(str(doc.get("captured_at") or ""), str(doc.get("run_id") or ""),
                              rows))
    # By instant, not by string: captured_at carries the company's UTC offset
    # (-04:00 on Keystone), and a UTC-stamped snapshot sorts after a later
    # one as text. Unparseable stamps sort first.
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    found.sort(key=lambda s: _parse(s.captured_at) or epoch)
    history.snapshots = found[-limit:] if limit else found
    return history


def build_rows(deals: list[dict], *, captured_at: str, run_id: str,
               scores: dict[str, dict]) -> list[dict]:
    rows = []
    for deal in deals:
        score = scores.get(deal.get("id")) or {}
        rows.append({
            "deal_id": deal.get("id"),
            "captured_at": captured_at,
            **{k: deal.get(k) for k in ROW_FIELDS},
            # 'Deal from <uuid>' titles (B4) shown by party name, as everywhere else.
            "display_title": display_title(deal),
            "rot_score": score.get("rot_score"),
            "rot_band": score.get("rot_band"),
            "rot_comment": score.get("rot_comment"),
        })
    return rows


def snapshot_document(rows: list[dict], *, tenant: str, captured_at: str, run_id: str,
                      reason: str = "manual") -> dict:
    """The file written next to result.json. `reason` follows the proposed
    table's `snapshot_reason` (scheduled | manual)."""
    return {"tenant": tenant, "captured_at": captured_at, "run_id": run_id,
            "snapshot_reason": reason, "captured_by": f"pipeline_agent run={run_id}",
            "schema": "gapreport/deals_snapshot_schema.json (DealSnapshot), stored on disk",
            "rows": rows}


def write_snapshot(run_dir: str | Path, document: dict) -> Path:
    path = Path(run_dir) / SNAPSHOT_FILE
    path.write_text(json.dumps(document, indent=1, default=str))
    return path


def stage_rank(stage: str, order: list[str] | tuple[str, ...]) -> int | None:
    return order.index(stage) if stage in order else None


def derive(deal: dict, history: SnapshotHistory | None, now: dt.datetime,
           stage_order: list[str] | tuple[str, ...]) -> dict:
    """What the earlier snapshots say about one deal.

    * `snapshots_seen` - how many earlier snapshots contain the deal;
    * `stage_entry` - `observed` when the stage changed between two
      snapshots (entry lies between them; the later one is used), else
      `lower_bound` (the stage has not changed since the first snapshot that
      saw the deal), else `none`;
    * `regressions` - backward moves in the pipeline's stage order, including
      the move into the current stage;
    * `close_date_pushes` - each time `expected_close_date` moved later.
    """
    stage = str(deal.get("stage") or "")
    past = history.rows_for(str(deal.get("id"))) if history else []
    out: dict[str, Any] = {"snapshots_seen": len(past),
                           "observed_since": past[0][0] if past else None,
                           "stage_entry": {"basis": "none"},
                           "regressions": [], "close_date_pushes": []}
    if not past:
        return out

    timeline = past + [(now.isoformat(), deal)]
    entered_at, entered_basis = past[0][0], "lower_bound"
    for (t_prev, prev), (t_cur, cur) in zip(timeline, timeline[1:]):
        before, after = str(prev.get("stage") or ""), str(cur.get("stage") or "")
        if before != after:
            entered_at, entered_basis = t_cur, "observed"
            r_before, r_after = stage_rank(before, stage_order), stage_rank(after, stage_order)
            if r_before is not None and r_after is not None and r_after < r_before:
                out["regressions"].append({"from": before, "to": after, "seen_at": t_cur})
        d_before, d_after = _day(prev.get("expected_close_date")), \
            _day(cur.get("expected_close_date"))
        if d_before and d_after and d_after > d_before:
            out["close_date_pushes"].append({"from": d_before, "to": d_after, "seen_at": t_cur})
    # Only the latest stage stay matters; an earlier change to another stage
    # does not date entry into this one unless it was the last change.
    entered = _parse(entered_at)
    out["stage_entry"] = {
        "basis": entered_basis,
        "stage": stage,
        "since": entered_at,
        "days": None if entered is None else max(0, (now - entered).days),
    }
    return out


def diff(previous: Snapshot | None, rows: list[dict]) -> dict:
    """What changed since the last snapshot of this tenant."""
    if previous is None:
        return {"previous_snapshot": None,
                "note": "first snapshot for this tenant - nothing to compare against yet"}
    cur = {r["deal_id"]: r for r in rows if r.get("deal_id")}
    before = previous.rows
    changes: dict[str, list] = {"stage": [], "value": [], "expected_close_date": [],
                                "owner": [], "rot_band": []}
    for deal_id in sorted(set(cur) & set(before)):
        for key in changes:
            old, new = before[deal_id].get(key), cur[deal_id].get(key)
            if old != new:
                changes[key].append({"deal_id": deal_id, "from": old, "to": new})
    return {"previous_snapshot": previous.captured_at,
            "previous_run_id": previous.run_id,
            "new_deals": sorted(set(cur) - set(before)),
            "gone_deals": sorted(set(before) - set(cur)),
            "changed": {k: v for k, v in changes.items() if v}}
