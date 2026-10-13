# QA v1 · issue 8 · head 35ef0a2

| AC | Result | Proof |
|---|---|---|
| 1. doc records model id, status, latency | pass | `docs/bedrock-check.md` table: `amazon.nova-micro-v1:0`, 200, 1.39 s |
| 2. recorded status is 200 | pass | table row `HTTP status 200`; matches the owner's terminal output (`200 1.393676s`) and the owner evidence comment on #8 |
| 3. PR body has `needs-you:` line with the command | pass | `sb:risk` block of PR 16 |
| 4. no file outside `docs/` | pass | `git diff --name-only origin/main...HEAD` lists only `docs/bedrock-check.md` |

Extra check: the `BEDROCK_MODEL_ID` finding holds (`workload/agent.py:57` reads `env["BEDROCK_MODEL_ID"]`, no default).
No credential, token or account id in the doc.

The real Bedrock request needs the owner's AWS sign-in, so QA did not re-run it. Evidence is the owner's request, not a QA re-run.
Local tests: git diff --name-only origin/main...HEAD; grep -nE 'AKIA|ASIA|[0-9]{12}' docs/bedrock-check.md
