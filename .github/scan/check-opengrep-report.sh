#!/usr/bin/env bash
set -euo pipefail

# Fail closed on missing/malformed reports, parser errors, and active findings.
# Ignored findings may be included in Opengrep JSON depending on its version.
report_path="${1:?Usage: check-opengrep-report.sh REPORT_PATH}"
jq -e '
  (.errors | type == "array" and length == 0)
  and (.results | type == "array")
  and ([.results[] | select(.extra.is_ignored != true)] | length == 0)
' "$report_path" > /dev/null || {
  echo "Opengrep report contains errors/findings or is invalid: $report_path" >&2
  exit 1
}
