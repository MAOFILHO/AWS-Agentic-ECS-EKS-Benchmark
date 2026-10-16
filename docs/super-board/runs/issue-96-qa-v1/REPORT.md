# QA v1 · issue #96 · docs only

Checked `docs/phase5-smoke-check.md` and `docs/SPEC.md` at `ac80b64` against local `results/smoke5/` (gitignored) and `runs/smoke5/commands.jsonl`.

| AC | Result | Proof |
|---|---|---|
| 1 gate estimate and "no" test | pass | doc section Gate: USD 0.13, smoke0 stopped, zero stacks, 10 cost lines match the issue |
| 2 run time and spend | pass | about 32 min vs 35, USD 0.13, under USD 2; 503.3 s + 759.8 s = 21.0 min |
| 3 figures with sources | pass | all 9 figures equal `metrics.json` values (rounded) and appear in `report.md`; no extra number in the report |
| 4 zero leftovers | pass | doc Results table row; not re-checked live (AWS connector not authorised in this session) |
| 5 `needs-you:` line | pass | PR body Risk block lists a to g in order |
| 6 no account id or email | pass | grep for 12 digits and `@host.tld` on both files returns nothing; PR touches only those 2 files |
| 7 SPEC says 35 min | pass | `docs/SPEC.md:20` and `:130`; no "25 min" left |

Timing check: commands.jsonl seconds 157.56, 66.87, 278.831, 759.787 match the doc.
Local tests: grep -E "[0-9]{12}|@[A-Za-z0-9.-]+\.[a-z]{2,}" docs/phase5-smoke-check.md docs/SPEC.md
