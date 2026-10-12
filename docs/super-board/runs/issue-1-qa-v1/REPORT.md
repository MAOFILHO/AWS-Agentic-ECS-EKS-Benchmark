# QA v1 · issue #1 · head 1641a31

| AC | Result | Proof |
|---|---|---|
| 1 SPEC ends with "Open questions" | ✅ | `check-ac.log`: last H2 is `## 13. Open questions`, 8 items |
| 2 5 ADRs with the ticket's names | ✅ | `check-ac.log`: all 5 files present and non-empty |
| 3 3 Mermaid diagrams render on GitHub | ✅ | 3 GitHub mermaid frames load, no render error (`github-diagram-*.jpg`); mermaid-cli 11.4.2 renders all 3 (`architecture-1..3.png`) |
| 4 No file outside `docs/` | ✅ | `git diff --name-only origin/main...HEAD` lists only `docs/` |

Red check: deleting an ADR and appending a section after "Open questions" turns the script red (AC1 and AC2 fail, exit 1).

Finding (Medium, not an AC fail): GitHub's Mermaid viewer clips most node labels
(for example `00-bootstrap: OI…`, `Report and actua…`). The same source renders unclipped in
mermaid-cli. Fix would be shorter labels or `<br/>` breaks in `docs/architecture.md`.

Rerun: `bash docs/super-board/runs/issue-1-qa-v1/check-ac.sh`
