#!/usr/bin/env bash
# QA checks for issue #4 (repo layout, pyproject, Makefile). Run from the repo root.
set -u
fail=0
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }

# AC1: 9 directories, each tracked with a .gitkeep
for d in infra/cfn infra/helm/workload workload advisor loadtests runs results web tests; do
  git ls-files --error-unmatch "$d/.gitkeep" >/dev/null 2>&1 && ok "AC1 $d/.gitkeep tracked" || bad "AC1 $d/.gitkeep not tracked"
done

# AC2: uv sync exits 0 on Python 3.12
rm -rf .venv
uv sync >/dev/null 2>&1; rc=$?
[ $rc -eq 0 ] && ok "AC2 uv sync exit 0" || bad "AC2 uv sync exit $rc"
py=$(uv run python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
[ "$py" = "3.12" ] && ok "AC2 interpreter is $py" || bad "AC2 interpreter is '$py'"

# AC3: each target prints the message and fails. The recipe exits 1; GNU make reports a failed recipe as exit 2.
for t in bootstrap benchmark teardown report; do
  out=$(make "$t" 2>&1); rc=$?
  echo "$out" | grep -q "not implemented yet" && ok "AC3 make $t prints message" || bad "AC3 make $t no message"
  echo "$out" | grep -q "Error 1" && ok "AC3 make $t recipe exit 1 (make exit $rc)" || bad "AC3 make $t recipe not exit 1"
  [ $rc -ne 0 ] || bad "AC3 make $t exited 0"
done

# AC4: .gitignore ignores .venv and the local env file; results/ ignored except results/sample/
grep -qx '\.venv' .gitignore && ok "AC4 .venv listed" || bad "AC4 .venv missing"
grep -qx '\.e[n]v' .gitignore && ok "AC4 local env file listed" || bad "AC4 local env file missing"
git check-ignore -q results/x && ok "AC4 results/x ignored" || bad "AC4 results/x not ignored"
git check-ignore -q results/sample/x && bad "AC4 results/sample/x ignored" || ok "AC4 results/sample/x not ignored"

exit $fail
