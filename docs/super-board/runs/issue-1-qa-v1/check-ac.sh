#!/usr/bin/env bash
# AC checks for issue #1. Run from the repo root: bash docs/super-board/runs/issue-1-qa-v1/check-ac.sh
set -u
base="${BASE:-origin/main}"
fail=0
ok()  { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }

# AC1: SPEC.md exists and its last H2 section is "Open questions" with content
if [ -f docs/SPEC.md ]; then
  last_h2=$(grep -E '^## ' docs/SPEC.md | tail -1)
  body=$(awk '/^## /{s=$0; buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' docs/SPEC.md | grep -c '[^[:space:]]')
  if echo "$last_h2" | grep -qi 'open questions' && [ "$body" -gt 0 ]; then ok "AC1 last section: $last_h2 ($body non-blank lines)"; else bad "AC1 last section is '$last_h2'"; fi
else bad "AC1 docs/SPEC.md missing"; fi

# AC2: exactly the 5 ADR names from the ticket
for f in 0001-cloudformation-plus-helm 0002-fargate-as-matrix-axis 0003-mock-llm-mode \
         0004-deterministic-analyst-llm-reporter 0005-oidc-no-access-keys; do
  [ -s "docs/adr/$f.md" ] && ok "AC2 docs/adr/$f.md" || bad "AC2 docs/adr/$f.md missing or empty"
done

# AC3: 3 mermaid blocks (render is checked by mmdc separately)
n=$(grep -c '^```mermaid' docs/architecture.md 2>/dev/null || echo 0)
[ "$n" -eq 3 ] && ok "AC3 $n mermaid blocks" || bad "AC3 found $n mermaid blocks, want 3"

# AC4: no file outside docs/ in the branch diff
outside=$(git diff --name-only "$base"...HEAD | grep -v '^docs/' || true)
[ -z "$outside" ] && ok "AC4 diff touches only docs/" || bad "AC4 files outside docs/: $outside"

exit $fail
