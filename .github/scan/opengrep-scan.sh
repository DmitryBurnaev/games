#!/usr/bin/env bash
set -euo pipefail

# Shared arguments keep project and positive-control scans identical.
report_path="${1:?Usage: opengrep-scan.sh REPORT_PATH [TARGET]}"
target="${2:-.}"
scan_args=(--config "${OPENGREP_RULES_DIR:-opengrep-rules/python}")
while IFS= read -r rule || [[ -n "$rule" ]]; do
  rule="${rule%%#*}"
  rule="${rule//[[:space:]]/}"
  if [[ -n "$rule" ]]; then
    scan_args+=(--exclude-rule "$rule")
  fi
done < "${OPENGREP_EXCLUDE_RULES:-.github/scan/opengrep-exclude-rules.txt}"

opengrep scan "${scan_args[@]}" --json --output "$report_path" "$target"
