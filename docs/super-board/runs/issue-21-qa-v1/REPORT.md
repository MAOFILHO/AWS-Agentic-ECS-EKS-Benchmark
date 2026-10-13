# QA v1 · issue 21 · head 2cb95a2

| AC | Result | Proof |
|---|---|---|
| ci.yml job `cfn` runs cfn-lint and cfn-guard validate | pass | `.github/workflows/ci.yml` lines 32-52; CI run 38335620310 job `cfn` success |
| cfn-lint exits 0 on placeholder | pass | `uvx cfn-lint==1.57.2 infra/cfn/*.yaml` exit 0 (local) |
| cfn-guard fails on mutable ECR, passes on fixed copy | pass | CI steps "rejects a mutable ECR repository" and "accepts the fixed copy" success; fixtures differ only in ImageTagMutability |
| SPEC.md no longer says "not yet confirmed" | pass | `grep -c "not yet confirmed" docs/SPEC.md` is 0 |

Local: `uv run --locked ruff check` clean; `uv run --locked pytest` 10 passed.
cfn-guard is not installed locally; its proof comes from CI only.
