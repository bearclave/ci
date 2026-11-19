#!/usr/bin/env bash
# trivy over the plans in JSON, one report per unit.
#
# Usage: trivy-plans.sh <plan-json-dir> <report-dir>
#
# <plan-json-dir> holds <unit>/tfplan.json (terragrunt's --json-out-dir). Each
# report lands in <report-dir> as <unit with / as _>.json, with the unit in
# its "Unit" field. Exit code 3 when there is a HIGH or CRITICAL finding that
# .github/trivyignore.yaml does not accept, 0 when there is none, anything else when
# trivy itself failed.
#
# trivy turns a plan into a main.tf of its own, so every finding has that file
# as its target and the `paths` of an ignore entry cannot tell the units
# apart. In .github/trivyignore.yaml `paths` therefore lists units (<unit>/tfplan.json)
# and is applied here: each unit gets the entries that name it or name no unit
# at all.
#
# The checks are the ones built into the pinned trivy (--skip-check-update),
# so a run is repeatable; new checks arrive with a version bump.
set -euo pipefail

PLANS=${1:?missing plan directory}
REPORTS=${2:?missing report directory}
IGNORE="$PWD/.github/trivyignore.yaml"
[ -f "$IGNORE" ] || IGNORE="$PWD/.trivyignore.yaml"
UNIT_IGNORE=$(mktemp --suffix=.yaml)

mkdir -p "$REPORTS"
REPORTS=$(cd "$REPORTS" && pwd)
if [ ! -d "$PLANS" ]; then
  echo "No plan in JSON."
  exit 0
fi

found=0
while IFS= read -r plan; do
  report="$REPORTS/$(tr / _ <<<"${plan%/tfplan.json}").json"
  yq -o=json '.' "$IGNORE" |
    jq --arg plan "$plan" '{misconfigurations: [(.misconfigurations // [])[]
      | select((.paths // []) | length == 0 or any(.[]; . == $plan)) | del(.paths)]}' |
    yq -P '.' >"$UNIT_IGNORE"
  rc=0
  (cd "$PLANS" && trivy config --quiet --skip-check-update \
    --severity HIGH,CRITICAL --exit-code 3 --ignorefile "$UNIT_IGNORE" \
    --format json --output "$report" "$plan") || rc=$?
  case "$rc" in
    0) ;;
    3) found=1 ;;
    *) echo "trivy failed on $plan" >&2; exit "$rc" ;;
  esac
  # The unit goes into the report itself, so that the summary does not have
  # to guess it from trivy's target; the log shows what failed where.
  unit="${plan%/tfplan.json}"
  jq --arg unit "$unit" '.Unit = $unit' "$report" >"$report.tmp" && mv "$report.tmp" "$report"
  jq -r --arg unit "$unit" '.Results[]? | .Target as $t | .Misconfigurations[]?
    | select(.Status == "FAIL")
    | "\($unit): \(.Severity) \(.AVDID // .ID) \(.Title) [target \($t)]"' "$report"
done < <(cd "$PLANS" && find . -name tfplan.json | sed 's|^\./||' | sort)

[ "$found" -eq 0 ] || exit 3
