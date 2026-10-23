#!/usr/bin/env bash
set -euo pipefail

scanner="${1:?Usage: verify-scanner.sh gitleaks|opengrep}"
fixture_dir="$(mktemp -d "${TMPDIR:-/tmp}/games-scan-check.XXXXXX")"
trap 'rm -rf "$fixture_dir"' EXIT

case "$scanner" in
  gitleaks)
    # Generate a synthetic value at runtime; never commit a secret-like fixture.
    printf 'api_key = "%s"\n' "$(openssl rand -hex 24)" > "$fixture_dir/credential.txt"
    scan_status=0
    gitleaks dir "$fixture_dir" --gitleaks-ignore-path "$PWD/.gitleaksignore" \
      --redact=100 --exit-code 23 --report-format json \
      --report-path "$fixture_dir/results.json" || scan_status=$?
    if [[ "$scan_status" -ne 23 ]]; then
      echo "Expected Gitleaks to reject the synthetic secret; got $scan_status" >&2
      exit 1
    fi
    jq -e 'any(.[]; .RuleID == "generic-api-key")' "$fixture_dir/results.json" > /dev/null
    ;;
  opengrep)
    cat > "$fixture_dir/invalid_model.py" <<'PY'
from django.db import models

class InvalidModel(models.Model):
    value = models.IntegerField(blank=True)
PY
    bash .github/scan/opengrep-scan.sh "$fixture_dir/finding.json" "$fixture_dir/invalid_model.py"
    jq -e '
      (.errors | length == 0) and
      any(.results[]; .check_id == "opengrep-rules.python.django.correctness.nontext-field-must-set-null-true" and .extra.is_ignored != true)
    ' "$fixture_dir/finding.json" > /dev/null
    if bash .github/scan/check-opengrep-report.sh "$fixture_dir/finding.json"; then
      echo "Expected the Opengrep gate to reject an unsuppressed finding" >&2
      exit 1
    fi

    # Reproduce the Python 3.14 syntax that previously caused PartialParsing.
    # A wholly unparseable file can be silently skipped by this scanner version.
    cat > "$fixture_dir/invalid_syntax.py" <<'PY'
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

def read_settings():
    try:
        return {}
    except DatabaseError, OperationalError, ProgrammingError:
        return {}
PY
    bash .github/scan/opengrep-scan.sh "$fixture_dir/parsing.json" "$fixture_dir/invalid_syntax.py" || {
      test -s "$fixture_dir/parsing.json"
    }
    jq -e '.errors | length > 0' "$fixture_dir/parsing.json" > /dev/null
    if bash .github/scan/check-opengrep-report.sh "$fixture_dir/parsing.json"; then
      echo "Expected the Opengrep gate to reject an incomplete scan" >&2
      exit 1
    fi
    ;;
  *)
    echo "Unknown scanner: $scanner" >&2
    exit 1
    ;;
esac

echo "$scanner positive-control checks passed"
