#!/usr/bin/env bash
# Orchestrator. Runs local then remote, then writes results/SUMMARY.md.
#
# Usage:
#   ./smoke/run-all.sh           # both local and remote
#   ./smoke/run-all.sh local     # local only (includes setup + teardown)
#   ./smoke/run-all.sh remote    # remote only
#
# Local flow:
#   00-setup -> 01..15 -> 18 -> 17 (T17 stops the container) -> 99-teardown
# Remote flow:
#   01,02,03,04,06,12,15,16,18 in numeric order
#
# Returns 0 only if every test reports PASS or SKIP.
set -uo pipefail

SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SMOKE_DIR/results}"
mkdir -p "$RESULTS_DIR"
export RESULTS_DIR

MODE="${1:-all}"

run_script() {
    local script="$1"
    echo
    echo "=== $(basename "$script") ==="
    bash "$script"
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "(script exited with $rc)"
    fi
    return 0  # don't stop the suite on a single fail; SUMMARY tallies it
}

run_local() {
    local seq="00-setup 01-health 02-session-cookie 03-auth-matrix \
        04-slug-validation 05-ui-playwright 06-ssrf 07-xxe 08-artwork \
        09-rate-limits 10-destructive-rate 11-lockout 12-rss-public-paths \
        13-backup 14-patterns 15-log-hygiene 18-multi-worker 17-shutdown \
        99-teardown"
    for name in $seq; do
        local f="$SMOKE_DIR/local/${name}.sh"
        [ -x "$f" ] || chmod +x "$f"
        run_script "$f"
    done
}

run_remote() {
    local seq="01-health 02-session-cookie 03-auth-matrix 04-slug-validation \
        06-ssrf 12-rss-public-paths 15-log-hygiene 16-episode-processing 18-headers"
    for name in $seq; do
        local f="$SMOKE_DIR/remote/${name}.sh"
        [ -x "$f" ] || chmod +x "$f"
        run_script "$f"
    done
}

case "$MODE" in
    local)  run_local ;;
    remote) run_remote ;;
    all)    run_local; run_remote ;;
    *) echo "usage: $0 [local|remote|all]" >&2; exit 2 ;;
esac

# Build SUMMARY.md
SUMMARY="$RESULTS_DIR/SUMMARY.md"
{
    echo "# MinusPod 2.0.0 smoke results"
    echo
    echo "Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo
    echo "Mode: \`$MODE\`"
    echo
    echo "## Per-test outcomes"
    echo
    echo "| Test | Result | Pass/Total | Notes |"
    echo "|------|--------|------------|-------|"
    for f in "$RESULTS_DIR"/*.txt; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .txt)
        last=$(grep '^RESULT ' "$f" | tail -1)
        if [ -z "$last" ]; then
            outcome="NORESULT"
            counts=""
        else
            outcome=$(echo "$last" | awk '{print $3}')
            counts=$(echo "$last" | awk '{$1=$2=$3=""; print $0}' | sed -e 's/^ *//;s/ *$//')
        fi
        printf '| %s | %s | %s | |\n' "$name" "$outcome" "$counts"
    done
    echo
    echo "## Failure details"
    echo
    grep -h '^FAIL ' "$RESULTS_DIR"/*.txt 2>/dev/null \
        | sed 's/^/- /' || echo "_no failures_"
} > "$SUMMARY"

echo
echo "Wrote $SUMMARY"
fail_total=$(grep -c '^FAIL ' "$RESULTS_DIR"/*.txt 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
echo "Total FAIL lines across all tests: $fail_total"
[ "$fail_total" -eq 0 ]
