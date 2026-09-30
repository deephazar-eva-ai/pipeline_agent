"""The five-factor rot score from gapreport §5, degraded honestly.

crm_gap_fillup.md B3. Formula, weights, caps and bands are carried over from
`gapreport/crm_gapreport.md` §5.1 unchanged:

    rot_score = 0.35*S_stage + 0.25*S_activity + 0.20*S_slippage
              + 0.15*S_regression + 0.05*S_value

What changes is how each input is sourced, because this platform does not
store two of them:

| factor     | real when                                   | otherwise                    |
|------------|---------------------------------------------|------------------------------|
| activity   | a completed, non-agent contact exists       | band `unknown` (never contacted) |
| slippage   | `expected_close_date` is set                 | missing                      |
| value      | value > 0 and a p90 exists                   | missing ("unvalued")         |
| regression | an earlier snapshot saw the deal             | missing                      |
| stage      | a snapshot saw the stage change              | proxy: days since the deal opened (an upper bound) |

Rules, in order:

1. No completed contact at all -> band `unknown`, no number. This is the
   gap report's own Unknown band, and it is exactly the never-contacted deal.
2. Fewer than three REAL factors -> band `insufficient_evidence`, no number.
   A proxy never counts toward the three.
3. Otherwise the score is the weighted mean over the factors that exist
   (real or proxy), and the band follows §5.1: >= 0.75 critical,
   >= 0.50 at_risk, else healthy.

The score only adds evidence. It never takes a deal out of the rotting list;
it can only point at deals the rot check did not flag.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

WEIGHTS = {"stage": 0.35, "activity": 0.25, "slippage": 0.20, "regression": 0.15,
           "value": 0.05}
ACTIVITY_CAP_DAYS = 30
SLIPPAGE_CAP_DAYS = 45
AT_RISK, CRITICAL = 0.50, 0.75
MIN_REAL_FACTORS = 3
# "No regression" is only evidence after the deal has been watched this long.
# A regression actually seen counts at once; its absence over four minutes of
# history does not (measured 2026-09-30: counting it moved 9 of 25 Keystone
# bands between two runs minutes apart).
REGRESSION_MIN_HISTORY_DAYS = 7

BAND_CRITICAL = "critical"
BAND_AT_RISK = "at_risk"
BAND_HEALTHY = "healthy"
BAND_UNKNOWN = "unknown"
BAND_INSUFFICIENT = "insufficient_evidence"
BANDS = (BAND_CRITICAL, BAND_AT_RISK, BAND_HEALTHY, BAND_UNKNOWN, BAND_INSUFFICIENT)


def p90(values: list[float]) -> float | None:
    """Nearest-rank 90th percentile of the positive values, or None."""
    positive = sorted(v for v in values if v > 0)
    if not positive:
        return None
    rank = max(1, -(-9 * len(positive) // 10))  # ceil(0.9 * n)
    return positive[rank - 1]


def _factor(sub: float | None, status: str, **info: Any) -> dict:
    return {"sub_score": None if sub is None else round(min(max(sub, 0.0), 1.0), 3),
            "status": status, **info}


def score_deal(deal: dict, *, now: dt.datetime, contact_days: int | None,
               opened_days: int | None, stage_days_typical: int, value: float,
               value_p90: float | None, derived: dict,
               money: Callable[[float], str] = lambda v: f"{v:,.0f}") -> dict:
    """Score one open deal. `contact_days` is None when the deal has never
    had a completed contact; `derived` is `snapshot.derive(...)`."""
    factors: dict[str, dict] = {}

    # activity
    if contact_days is None:
        factors["activity"] = _factor(None, "missing", note="no completed contact on record")
    else:
        factors["activity"] = _factor(contact_days / ACTIVITY_CAP_DAYS, "real",
                                      days_since_contact=contact_days)

    # stage
    entry = derived.get("stage_entry") or {}
    typical = max(1, stage_days_typical)
    if entry.get("basis") == "observed" and entry.get("days") is not None:
        factors["stage"] = _factor(entry["days"] / (2 * typical), "real",
                                   days_in_stage=entry["days"], since=entry.get("since"),
                                   typical_stay_days=typical,
                                   note="stage change seen between two snapshots")
    elif opened_days is not None:
        factors["stage"] = _factor(opened_days / (2 * typical), "proxy",
                                   days_in_stage=opened_days, typical_stay_days=typical,
                                   note="no stage-entry date exists; days since the deal "
                                        "opened, an upper bound on time in stage"
                                   + (f"; unchanged across {derived.get('snapshots_seen')} "
                                      f"snapshot(s) since {str(entry.get('since'))[:10]}"
                                      if entry.get("basis") == "lower_bound" else ""))
    else:
        factors["stage"] = _factor(None, "missing", note="no stage-entry or opened date")

    # slippage
    close = str(deal.get("expected_close_date") or "")[:10]
    try:
        close_day = dt.date.fromisoformat(close)
    except ValueError:
        close_day = None
    if close_day is None:
        factors["slippage"] = _factor(None, "missing", note="no expected_close_date")
    else:
        past = max((now.date() - close_day).days, 0)
        factors["slippage"] = _factor(past / SLIPPAGE_CAP_DAYS, "real",
                                      days_past_close=past, expected_close_date=close)

    # regression
    since = derived.get("observed_since")
    since_ts = None
    if since:
        try:
            since_ts = dt.datetime.fromisoformat(str(since).replace("Z", "+00:00"))
            if since_ts.tzinfo is None:
                since_ts = since_ts.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            since_ts = None
    watched = None if since_ts is None else (now - since_ts).days
    if derived.get("regressions"):
        factors["regression"] = _factor(1.0, "real", regressions=derived["regressions"],
                                        observed_since=since)
    elif watched is not None and watched >= REGRESSION_MIN_HISTORY_DAYS:
        factors["regression"] = _factor(0.0, "real", regressions=[], observed_since=since,
                                        watched_days=watched)
    else:
        factors["regression"] = _factor(
            None, "missing",
            note=("no earlier snapshot - regression unobservable" if watched is None else
                  f"watched {watched} day(s); 'no regression' counts only after "
                  f"{REGRESSION_MIN_HISTORY_DAYS}"))

    # value
    if value > 0 and value_p90:
        factors["value"] = _factor(value / value_p90, "real", value=value, p90=value_p90)
    else:
        factors["value"] = _factor(None, "missing", note="deal is unvalued (value <= 0)")

    for name, f in factors.items():
        f["weight"] = WEIGHTS[name]
        f["contribution"] = (None if f["sub_score"] is None
                             else round(f["weight"] * f["sub_score"], 4))
    real = sum(1 for f in factors.values() if f["status"] == "real")
    out: dict[str, Any] = {"rot_score": None, "rot_band": None, "rot_comment": "",
                           "real_factors": real, "factors": factors,
                           "model": "gapreport §5.1 (weights 0.35/0.25/0.20/0.15/0.05)"}

    if contact_days is None:
        out["rot_band"] = BAND_UNKNOWN
        out["rot_comment"] = ("Rot score unavailable - no completed contact exists for this "
                              "deal (never contacted).")
        return out
    if real < MIN_REAL_FACTORS:
        missing = [n for n, f in factors.items() if f["status"] != "real"]
        out["rot_band"] = BAND_INSUFFICIENT
        out["rot_comment"] = (f"Rot score withheld - only {real} real factor(s); "
                              f"{', '.join(missing)} missing or proxied.")
        return out

    present = {n: f for n, f in factors.items() if f["sub_score"] is not None}
    total_weight = sum(f["weight"] for f in present.values())
    score = sum(f["contribution"] for f in present.values()) / total_weight
    out["rot_score"] = round(score, 3)
    out["rot_band"] = (BAND_CRITICAL if score >= CRITICAL else
                       BAND_AT_RISK if score >= AT_RISK else BAND_HEALTHY)
    out["weights_used"] = round(total_weight, 2)
    out["rot_comment"] = _comment(present, score, deal, money)
    return out


def _comment(present: dict[str, dict], score: float, deal: dict,
             money: Callable[[float], str]) -> str:
    """Name the factor with the largest WEIGHTED contribution; ties break in
    weight order (stage, activity, slippage, regression, value) - §5.2."""
    order = list(WEIGHTS)
    name = max(present, key=lambda n: (present[n]["contribution"], -order.index(n)))
    f = present[name]
    tail = f"(rot score {score:.2f})"
    if name == "stage":
        kind = "" if f["status"] == "real" else " (upper bound: no stage-entry date exists)"
        return (f"Primarily driven by time in current stage: {f['days_in_stage']} days in "
                f"{deal.get('stage')} vs. a {f['typical_stay_days']}-day typical stay"
                f"{kind} {tail}.")
    if name == "activity":
        return (f"Primarily driven by inactivity: no completed contact in "
                f"{f['days_since_contact']} days {tail}.")
    if name == "slippage":
        return (f"Primarily driven by close-date slippage: {f['days_past_close']} days past the "
                f"expected close date {tail}.")
    if name == "regression":
        return f"Primarily driven by stage regression: this deal moved backward a stage {tail}."
    return (f"Primarily driven by deal value: {money(f['value'])} deal size, among the "
            f"largest in the open pipeline {tail}.")
