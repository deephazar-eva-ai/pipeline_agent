#!/usr/bin/env python3
"""Direct-snapshot verifier for the write-diff gates of IT-01.

It intentionally reports every check separately and exits nonzero for any
failed gate. Read/judgement checks are retained as review entries until their
result.json schema is frozen by a first live run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def rows(run_dir: Path, snapshot: str, entity: str) -> dict[str, dict]:
    data = json.loads((run_dir / snapshot / f"{entity}.json").read_text())
    return {str(row["id"]): row for row in data if isinstance(row, dict) and row.get("id")}


def report(name: str, passed: bool, detail: str) -> dict:
    return {"check": name, "passed": passed, "detail": detail}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    root = Path(args.run_dir)
    fixtures = json.loads((root / "fixtures.json").read_text())
    fixture_deals = set(fixtures["deals"].values())
    fixture_parties = set(fixtures["parties"].values())
    s2, s3, s4, s5 = (rows(root, label, "Activity") for label in ("S2", "S3", "S4", "S5"))
    new_c = {key: value for key, value in s3.items() if key not in s2}
    agent_c = [row for row in new_c.values() if "created_by=pipeline_agent" in str(row.get("description", ""))]
    # The D10 adversary is intentionally party-only. It is fixture-scoped by
    # its recorded id and party, even though its deal_id must be null.
    party_only_ids = {fixtures.get("activities", {}).get("D10-concurrent")} - {None}
    c_rows_scoped = all(
        row.get("deal_id") in fixture_deals or
        (row.get("id") in party_only_ids and row.get("party_id") in fixture_parties)
        for row in new_c.values()
    )
    checks = [
        report("C-W1", len(agent_c) == 3 and {r.get("deal_id") for r in agent_c} ==
               {fixtures["deals"][key] for key in ("D3", "D9a", "D14")},
               f"agent-created Activities: {len(agent_c)}"),
        report("C-W7", s4 == s3, f"Run D activity diff: {len(set(s4) ^ set(s3))} row id(s)"),
        report("C-W8", s5 == s4, f"Run E activity diff: {len(set(s5) ^ set(s4))} row id(s)"),
        report("C-W10", c_rows_scoped,
               "Run C new Activities are fixture-scoped (including the D10 party-only adversary)"),
    ]
    output = {"checks": checks, "passed": all(item["passed"] for item in checks),
              "manual_review": ["C-R1 through C-R11 require result.json schema review", "C-W2 through C-W6 and C-W9 require the corresponding snapshots and tenant artifact"]}
    (root / "verification.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output["passed"] else 1)


if __name__ == "__main__":
    main()
