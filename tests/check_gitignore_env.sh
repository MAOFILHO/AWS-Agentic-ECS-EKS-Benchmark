#!/usr/bin/env bash
# Checks the .gitignore env rules: variants ignored, the example file tracked.
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
check() { # <path> <expected exit>
  git check-ignore -q "$1"; got=$?
  if [ "$got" -ne "$2" ]; then echo "FAIL $1: exit $got, want $2"; fail=1; else echo "ok   $1"; fi
}
check .env 0
check .env.local 0
check .env.production 0
check .env.example 1
exit "$fail"
