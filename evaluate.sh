#!/usr/bin/env bash
# Run the full acceptance suite and print an explicit pass/fail summary.
#
#   ./evaluate.sh              # functional tests + 50k performance test
#   ./evaluate.sh --quick      # functional tests only
#
# Exit code is 0 only when every selected check passes.
set -o pipefail
cd "$(dirname "$0")"

QUICK=0
if [[ "${1:-}" == "--quick" ]]; then
  QUICK=1
fi

functional_status=99
performance_status=99

echo "== Functional test suite =="
python -m pytest -q -m "not performance"
functional_status=$?

if [[ "$QUICK" -eq 1 ]]; then
  performance_status=0
  performance_label="SKIPPED (--quick)"
else
  echo
  echo "== 50,000-event performance test =="
  python -m pytest -q -m performance -s
  performance_status=$?
  performance_label="$([[ "$performance_status" -eq 0 ]] && echo PASS || echo FAIL)"
fi

echo
echo "================ ZENITH EVALUATION SUMMARY ================"
echo "Functional tests : $([[ "$functional_status" -eq 0 ]] && echo PASS || echo FAIL) (exit $functional_status)"
echo "Performance test : $performance_label"
echo "==========================================================="

if [[ "$functional_status" -eq 0 && "$performance_status" -eq 0 ]]; then
  echo "RESULT: PASS"
  exit 0
fi

echo "RESULT: FAIL"
exit 1
