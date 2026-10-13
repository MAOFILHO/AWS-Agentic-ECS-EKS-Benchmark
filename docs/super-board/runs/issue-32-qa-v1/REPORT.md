# QA v1 · issue 32 · head 51ac34c

| AC | Command | Result |
|---|---|---|
| 1 | `uv run --locked python -c "import awscrt"` | exit 0, awscrt 0.37.0 |
| 2 | `uv lock --check` | exit 0 |
| 3 | `uv run --locked pytest` / `ruff check` | 27 passed / All checks passed |
| 4 | PR #34 CI `image` job | pass (cfn, lint, test also pass) |
