#!/usr/bin/env bash
# reset.sh — start fresh from a logging and context standpoint
#
# Clears all generated run artifacts, decision history, market context
# snapshots, scan outputs, and the strategy document so the next run
# starts with a completely clean slate.
#
# What is cleared:
#   data/runs/            — all per-run artifact folders, including:
#                             00-market-selection.json  (Stage 0 selection record)
#                             00-request.md / 00-history.md
#                             01 through 12 stage artifacts and logs
#   data/reports/         — old report files
#   data/scans/           — market scanner output (scan-markets command)
#   data/market_contexts/ — cached per-market context snapshots
#   data/decision_log.jsonl — the append-only decision history log
#                             (also drives deduplication in Stage 0)
#   docs/strategy.md      — the live strategy document (regenerated on next run)
#
# What is preserved by default:
#   data/performance/     — your account trade history and PnL CSVs
#                           (these come from the Polymarket API and represent
#                           real trade history; regenerating them costs API calls)
#
# To also clear performance data, run with the --full flag:
#   ./scripts/reset.sh --full
#
# Usage:
#   ./scripts/reset.sh           # soft reset (preserve performance data)
#   ./scripts/reset.sh --full    # hard reset (clear everything including performance)
#   ./scripts/reset.sh --dry-run # show what would be deleted without deleting

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# ── Parse flags ──────────────────────────────────────────────────────────────

FULL_RESET=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --full)    FULL_RESET=true ;;
    --dry-run) DRY_RUN=true ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--full] [--dry-run]"
      exit 1
      ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

delete() {
  local path="$1"
  if [ "$DRY_RUN" = true ]; then
    echo "  [dry-run] would delete: $path"
  else
    rm -rf "$path"
  fi
}

delete_contents() {
  # Delete contents of a directory but keep the directory itself.
  # Preserves .gitkeep files so git still tracks the empty directory.
  local dir="$1"
  if [ ! -d "$dir" ]; then
    return
  fi
  if [ "$DRY_RUN" = true ]; then
    local count
    count=$(find "$dir" -mindepth 1 -not -name '.gitkeep' | wc -l | tr -d ' ')
    echo "  [dry-run] would delete $count item(s) inside: $dir"
  else
    find "$dir" -mindepth 1 -not -name '.gitkeep' -delete 2>/dev/null || true
  fi
}

ensure_gitkeep() {
  local dir="$1"
  if [ "$DRY_RUN" = false ] && [ -d "$dir" ]; then
    touch "$dir/.gitkeep"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
if [ "$DRY_RUN" = true ]; then
  echo "╔══════════════════════════════════════════════════╗"
  echo "║   polymarket-agent reset  [DRY RUN — no changes] ║"
  echo "╚══════════════════════════════════════════════════╝"
elif [ "$FULL_RESET" = true ]; then
  echo "╔══════════════════════════════════════════════════╗"
  echo "║   polymarket-agent reset  [FULL — including perf] ║"
  echo "╚══════════════════════════════════════════════════╝"
else
  echo "╔══════════════════════════════════════════════════╗"
  echo "║   polymarket-agent reset  [soft — keeping perf]  ║"
  echo "╚══════════════════════════════════════════════════╝"
fi
echo ""

# ── Clear run artifacts ───────────────────────────────────────────────────────

echo "Clearing run artifacts (data/runs/)..."
delete_contents "data/runs"
ensure_gitkeep  "data/runs"

echo "Clearing reports (data/reports/)..."
delete_contents "data/reports"
ensure_gitkeep  "data/reports"

echo "Clearing scan outputs (data/scans/)..."
delete_contents "data/scans"
ensure_gitkeep  "data/scans"

# ── Clear shared context artifacts ───────────────────────────────────────────

echo "Clearing market context snapshots (data/market_contexts/)..."
delete_contents "data/market_contexts"
ensure_gitkeep  "data/market_contexts"

echo "Clearing decision history (data/decision_log.jsonl)..."
delete "data/decision_log.jsonl"

echo "Clearing strategy document (docs/strategy.md)..."
delete "docs/strategy.md"

# ── Optionally clear performance data ────────────────────────────────────────

if [ "$FULL_RESET" = true ]; then
  echo "Clearing performance data (data/performance/)..."
  if [ "$DRY_RUN" = true ]; then
    perf_count=$(find "data/performance" -mindepth 1 -not -name '.gitkeep' | wc -l | tr -d ' ')
    echo "  [dry-run] would delete $perf_count item(s) inside: data/performance"
  else
    find "data/performance" -mindepth 2 -delete 2>/dev/null || true
    find "data/performance" -mindepth 1 -maxdepth 1 -type d -empty -delete 2>/dev/null || true
  fi
  ensure_gitkeep "data/performance"
else
  echo "Preserving performance data (data/performance/) — use --full to clear"
  # Still clear per-run performance snapshots; keep the live CSVs and latest_summary
  if [ -d "data/performance" ]; then
    find "data/performance" -type d -name "snapshots" | while read -r snap_dir; do
      if [ "$DRY_RUN" = true ]; then
        snap_count=$(find "$snap_dir" -mindepth 1 | wc -l | tr -d ' ')
        echo "  [dry-run] would delete $snap_count snapshot(s) in: $snap_dir"
      else
        find "$snap_dir" -mindepth 1 -delete 2>/dev/null || true
      fi
    done
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
if [ "$DRY_RUN" = true ]; then
  echo "Done. (dry run — nothing was changed)"
else
  echo "Done. The project is reset to a clean state."
  echo ""
  echo "Next steps:"
  echo "  Run a market scan:     python3 main.py scan-markets --limit 200 --top 20 --print-table"
  echo "  Run a full workflow:   python3 main.py"
  echo "  Run a batch workflow:  python3 main.py batch-run --max-markets 5 --max-approved 1"
fi
echo ""
