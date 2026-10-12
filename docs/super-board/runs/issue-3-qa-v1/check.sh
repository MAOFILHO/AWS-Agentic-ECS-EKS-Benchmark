#!/usr/bin/env bash
# Issue #3 AC checks. Run from repo root: bash docs/super-board/runs/issue-3-qa-v1/check.sh
set -u
fail=0
ok() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }
S=docs/SPEC.md
# AC1: Decisions section with 8 numbered answers, no Open questions section
grep -qE '^## [0-9]+\. Decisions' "$S" && ok "AC1 Decisions heading" || bad "AC1 Decisions heading"
n=$(awk '/^## [0-9]+\. Decisions/{f=1;next} /^## /{f=0} f&&/^[0-9]+\. \*\*/{c++} END{print c+0}' "$S")
[ "$n" = 8 ] && ok "AC1 8 answers ($n)" || bad "AC1 answers=$n"
grep -qiE '^#+ .*open questions' "$S" && bad "AC1 Open questions heading remains" || ok "AC1 no Open questions heading"
# AC2
grep -q 'USD 15.00' "$S" && ok "AC2 USD 15.00" || bad "AC2 USD 15.00"
grep -q 'BudgetEmail' "$S" && ok "AC2 BudgetEmail" || bad "AC2 BudgetEmail"
# AC3: tracked files (excluding agent tooling under .claude) hold no email or key
hits=$(git ls-files | grep -v '^\.claude/' | grep -v '\.pdf$' | xargs grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|[ps]k-lf-' 2>/dev/null | grep -v 'check.sh' || true)
[ -z "$hits" ] && ok "AC3 no email or key" || { bad "AC3"; echo "$hits"; }
# AC4
out=$(git diff --name-only origin/main...HEAD | grep -v '^docs/' || true)
[ -z "$out" ] && ok "AC4 only docs/" || { bad "AC4"; echo "$out"; }
exit $fail
