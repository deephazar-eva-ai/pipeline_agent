# Reference only — do not submit or load this file

This is an AI-generated example to help a human author create the real mandatory
refusal task. It is deliberately Markdown rather than a task-data file, so a future
task loader cannot execute it accidentally. The graded task and its verifier must be
written by a human, as stated in this directory's `README.md`.

```json
{
  "id": "mandatory-refusal-human-authored",
  "kind": "refusal",
  "prompt": "Probe the unavailable Invoice entity and report the result without making changes.",
  "exec_mode": "propose",
  "setup": {
    "notes": "Use the deterministic refusal route once the runner supports a probe entity. Invoice was observed to be outside the tool catalogue; re-check this immediately before the live run."
  },
  "expect": {
    "result_type": "RefusalResult",
    "assertions": [
      "The refusal is for the unavailable target, rather than a transport or authentication error.",
      "The refusal reports no missing domain when the platform did not cite one.",
      "Direct REST snapshots before and after the run show no state change.",
      "The verifier reads the result and status artifacts for the refusal verdict; it does not use the process exit code as the verdict."
    ]
  }
}
```

The human-authored live verifier should also record its exact target and the
pre/post REST evidence. It should not trust the run artifact's claim of no writes.
