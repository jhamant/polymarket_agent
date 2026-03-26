"""
Gap 6: Risk Limits and Portfolio-Level Controls

Loads risk_limits.json and enforces system-level guardrails before any
run enters the research pipeline. These controls operate independently of
the AI quality gate — the quality gate evaluates individual market merit,
while risk limits enforce portfolio-level constraints.

risk_limits.json schema:
{
  "max_open_positions": int,            # hard cap on total open positions
  "max_position_size_usd": float,       # per-trade size ceiling
  "max_exposure_per_family_usd": float, # per-family total exposure cap
  "daily_loss_limit_usd": float,        # circuit-breaker: stop if daily PnL < -N
  "max_exposure_total_usd": float,      # total portfolio exposure ceiling
  "family_caps": {                      # optional per-family overrides
    "crypto_onchain": float,
    "sports_official_data": float,
    ...
  }
}

All fields are optional. Missing fields mean "no limit for this dimension".
An empty or missing risk_limits.json means limits are unconfigured — the
runner logs a warning but does not block the run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LIMITS_PATH = Path("risk_limits.json")


@dataclass
class RiskLimits:
    max_open_positions: int | None = None
    max_position_size_usd: float | None = None
    max_exposure_per_family_usd: float | None = None
    daily_loss_limit_usd: float | None = None
    max_exposure_total_usd: float | None = None
    family_caps: dict[str, float] = field(default_factory=dict)
    configured: bool = False
    source_path: str = ""

    def cap_for_family(self, family_key: str) -> float | None:
        """Return the effective exposure cap for a family (specific cap takes priority)."""
        if family_key in self.family_caps:
            return self.family_caps[family_key]
        return self.max_exposure_per_family_usd

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "source_path": self.source_path,
            "max_open_positions": self.max_open_positions,
            "max_position_size_usd": self.max_position_size_usd,
            "max_exposure_per_family_usd": self.max_exposure_per_family_usd,
            "daily_loss_limit_usd": self.daily_loss_limit_usd,
            "max_exposure_total_usd": self.max_exposure_total_usd,
            "family_caps": self.family_caps,
        }


def load_risk_limits(path: Path) -> RiskLimits:
    """
    Load risk limits from JSON. Returns an unconfigured RiskLimits if the
    file does not exist or cannot be parsed.
    """
    if not path.exists():
        logger.warning("risk_limits.json not found at %s — running without portfolio-level controls", path)
        return RiskLimits(configured=False, source_path=str(path))

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to parse risk_limits.json at %s: %s", path, exc)
        return RiskLimits(configured=False, source_path=str(path))

    logger.debug("Loaded risk limits from %s", path)
    return RiskLimits(
        configured=True,
        source_path=str(path),
        max_open_positions=raw.get("max_open_positions"),
        max_position_size_usd=raw.get("max_position_size_usd"),
        max_exposure_per_family_usd=raw.get("max_exposure_per_family_usd"),
        daily_loss_limit_usd=raw.get("daily_loss_limit_usd"),
        max_exposure_total_usd=raw.get("max_exposure_total_usd"),
        family_caps=raw.get("family_caps", {}),
    )


@dataclass
class ExposureSnapshot:
    """Current portfolio state extracted from performance data."""

    open_positions_count: int = 0
    total_exposure_usd: float = 0.0
    family_exposure_usd: dict[str, float] = field(default_factory=dict)
    daily_pnl_usd: float = 0.0
    configured: bool = False


def load_exposure_snapshot(performance_summary_path: Path) -> ExposureSnapshot:
    """
    Read the current portfolio exposure from the performance summary JSON.
    Returns a zero-exposure snapshot if the file does not exist or account
    is unconfigured.
    """
    if not performance_summary_path.exists():
        logger.debug("Performance summary not found at %s — using zero-exposure snapshot", performance_summary_path)
        return ExposureSnapshot(configured=False)

    try:
        raw = json.loads(performance_summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse performance summary at %s: %s", performance_summary_path, exc)
        return ExposureSnapshot(configured=False)

    metrics = raw.get("summary_metrics", {})
    if not metrics.get("configured", False):
        logger.debug("Account unconfigured in performance summary — using zero-exposure snapshot")
        return ExposureSnapshot(configured=False)

    family_exposure: dict[str, float] = {}
    for entry in raw.get("family_rollup", []):
        fam = entry.get("family_key", "")
        exposure = float(entry.get("open_exposure_usd", 0.0))
        if fam:
            family_exposure[fam] = exposure

    total_exposure = sum(family_exposure.values())

    return ExposureSnapshot(
        configured=True,
        open_positions_count=int(metrics.get("open_positions_count", 0)),
        total_exposure_usd=total_exposure,
        family_exposure_usd=family_exposure,
        daily_pnl_usd=float(metrics.get("daily_pnl_usd", 0.0)),
    )


@dataclass
class RiskCheckResult:
    passed: bool
    violations: list[str]
    warnings: list[str]
    limits: RiskLimits
    exposure: ExposureSnapshot

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "warnings": self.warnings,
            "limits": self.limits.as_dict(),
            "exposure": {
                "configured": self.exposure.configured,
                "open_positions_count": self.exposure.open_positions_count,
                "total_exposure_usd": self.exposure.total_exposure_usd,
                "family_exposure_usd": self.exposure.family_exposure_usd,
                "daily_pnl_usd": self.exposure.daily_pnl_usd,
            },
        }


def check_risk_limits(
    limits: RiskLimits,
    exposure: ExposureSnapshot,
    family_key: str,
    proposed_size_usd: float,
) -> RiskCheckResult:
    """
    Check whether a proposed trade violates any configured risk limits.

    Returns a RiskCheckResult with passed=True if all limits are satisfied
    or unconfigured. Violations are hard blocks; warnings are informational.
    """
    violations: list[str] = []
    warnings: list[str] = []

    if not limits.configured:
        warnings.append(
            "risk_limits.json not found — running without portfolio-level controls. "
            "Create risk_limits.json before enabling live_trading."
        )
        return RiskCheckResult(passed=True, violations=violations, warnings=warnings, limits=limits, exposure=exposure)

    if not exposure.configured:
        warnings.append(
            "Account not configured — cannot verify exposure against limits. Proceeding in dry-run mode only."
        )

    # Hard limit: max open positions
    if limits.max_open_positions is not None:
        if exposure.open_positions_count >= limits.max_open_positions:
            violations.append(
                f"Open positions ({exposure.open_positions_count}) >= "
                f"max_open_positions ({limits.max_open_positions}). "
                "No new positions until an existing one closes."
            )

    # Hard limit: daily loss circuit-breaker
    if limits.daily_loss_limit_usd is not None:
        if exposure.daily_pnl_usd < -abs(limits.daily_loss_limit_usd):
            violations.append(
                f"Daily PnL (${exposure.daily_pnl_usd:.2f}) breaches "
                f"daily_loss_limit (${-limits.daily_loss_limit_usd:.2f}). "
                "Circuit-breaker active — no new trades today."
            )

    # Hard limit: total portfolio exposure ceiling
    if limits.max_exposure_total_usd is not None:
        projected_total = exposure.total_exposure_usd + proposed_size_usd
        if projected_total > limits.max_exposure_total_usd:
            violations.append(
                f"Proposed trade would bring total exposure to "
                f"${projected_total:.2f}, exceeding "
                f"max_exposure_total_usd (${limits.max_exposure_total_usd:.2f})."
            )

    # Hard limit: per-family exposure cap
    family_cap = limits.cap_for_family(family_key)
    if family_cap is not None:
        current_family = exposure.family_exposure_usd.get(family_key, 0.0)
        projected_family = current_family + proposed_size_usd
        if projected_family > family_cap:
            violations.append(
                f"Proposed trade would bring {family_key} exposure to "
                f"${projected_family:.2f}, exceeding cap of ${family_cap:.2f}."
            )

    # Warning: per-trade size ceiling
    if limits.max_position_size_usd is not None:
        if proposed_size_usd > limits.max_position_size_usd:
            violations.append(
                f"Proposed size (${proposed_size_usd:.2f}) exceeds "
                f"max_position_size_usd (${limits.max_position_size_usd:.2f}). "
                "Reduce size before executing."
            )

    passed = len(violations) == 0
    if passed:
        logger.info(
            "Risk check PASSED — family=%s size=$%.2f positions=%d total_exposure=$%.2f",
            family_key,
            proposed_size_usd,
            exposure.open_positions_count,
            exposure.total_exposure_usd,
        )
    else:
        for v in violations:
            logger.warning("Risk violation: %s", v)
        logger.warning("Risk check FAILED — %d violation(s) for family=%s", len(violations), family_key)
    for w in warnings:
        logger.info("Risk warning: %s", w)
    return RiskCheckResult(passed=passed, violations=violations, warnings=warnings, limits=limits, exposure=exposure)


def write_risk_check_artifact(result: RiskCheckResult, output_path: Path) -> dict[str, Any]:
    """Write the risk check result to a JSON artifact and return the dict."""
    payload = result.as_dict()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.debug("Risk check artifact written to %s", output_path)
    return payload
