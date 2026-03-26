"""Tests for polymarket_agent.risk_limits (Gap 6)."""

import json
from pathlib import Path

from polymarket_agent.risk_limits import (
    ExposureSnapshot,
    RiskLimits,
    check_risk_limits,
    load_exposure_snapshot,
    load_risk_limits,
    write_risk_check_artifact,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _limits(tmp_path: Path, **overrides) -> RiskLimits:
    defaults = {
        "max_open_positions": 5,
        "max_position_size_usd": 50.0,
        "max_exposure_per_family_usd": 100.0,
        "daily_loss_limit_usd": 50.0,
        "max_exposure_total_usd": 250.0,
        "family_caps": {"crypto_onchain": 80.0},
    }
    defaults.update(overrides)
    p = tmp_path / "risk_limits.json"
    p.write_text(json.dumps(defaults), encoding="utf-8")
    return load_risk_limits(p)


def _exposure(**kwargs) -> ExposureSnapshot:
    defaults = dict(
        configured=True,
        open_positions_count=2,
        total_exposure_usd=80.0,
        family_exposure_usd={"crypto_onchain": 40.0},
        daily_pnl_usd=0.0,
    )
    defaults.update(kwargs)
    return ExposureSnapshot(**defaults)


# ---------------------------------------------------------------------------
# load_risk_limits
# ---------------------------------------------------------------------------


def test_load_risk_limits_missing_file(tmp_path):
    result = load_risk_limits(tmp_path / "nonexistent.json")
    assert result.configured is False


def test_load_risk_limits_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    result = load_risk_limits(p)
    assert result.configured is False


def test_load_risk_limits_valid(tmp_path):
    lim = _limits(tmp_path)
    assert lim.configured is True
    assert lim.max_open_positions == 5
    assert lim.max_position_size_usd == 50.0
    assert lim.daily_loss_limit_usd == 50.0
    assert lim.family_caps["crypto_onchain"] == 80.0


def test_load_risk_limits_partial_fields(tmp_path):
    """Missing fields should leave those limits as None (unconstrained)."""
    p = tmp_path / "partial.json"
    p.write_text(json.dumps({"max_open_positions": 3}), encoding="utf-8")
    lim = load_risk_limits(p)
    assert lim.configured is True
    assert lim.max_open_positions == 3
    assert lim.max_position_size_usd is None
    assert lim.daily_loss_limit_usd is None


def test_cap_for_family_specific_cap(tmp_path):
    lim = _limits(tmp_path)
    # crypto_onchain has specific cap of 80
    assert lim.cap_for_family("crypto_onchain") == 80.0


def test_cap_for_family_fallback(tmp_path):
    lim = _limits(tmp_path)
    # sports_official_data uses the general family cap
    assert lim.cap_for_family("sports_official_data") == 100.0


def test_cap_for_family_no_general_cap(tmp_path):
    p = tmp_path / "no_general.json"
    p.write_text(json.dumps({"family_caps": {"crypto_onchain": 80.0}}), encoding="utf-8")
    lim = load_risk_limits(p)
    # Family without a specific cap, and no general cap set
    assert lim.cap_for_family("sports_official_data") is None


# ---------------------------------------------------------------------------
# load_exposure_snapshot
# ---------------------------------------------------------------------------


def test_load_exposure_snapshot_missing(tmp_path):
    snap = load_exposure_snapshot(tmp_path / "none.json")
    assert snap.configured is False


def test_load_exposure_snapshot_unconfigured_account(tmp_path):
    p = tmp_path / "perf.json"
    p.write_text(json.dumps({"summary_metrics": {"configured": False}}), encoding="utf-8")
    snap = load_exposure_snapshot(p)
    assert snap.configured is False


def test_load_exposure_snapshot_valid(tmp_path):
    data = {
        "summary_metrics": {
            "configured": True,
            "open_positions_count": 3,
            "daily_pnl_usd": -10.5,
        },
        "family_rollup": [
            {"family_key": "crypto_onchain", "open_exposure_usd": 45.0},
            {"family_key": "sports_official_data", "open_exposure_usd": 20.0},
        ],
    }
    p = tmp_path / "perf.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    snap = load_exposure_snapshot(p)
    assert snap.configured is True
    assert snap.open_positions_count == 3
    assert snap.daily_pnl_usd == -10.5
    assert snap.total_exposure_usd == 65.0
    assert snap.family_exposure_usd["crypto_onchain"] == 45.0


# ---------------------------------------------------------------------------
# check_risk_limits
# ---------------------------------------------------------------------------


def test_check_unconfigured_limits_always_passes(tmp_path):
    lim = load_risk_limits(tmp_path / "missing.json")  # unconfigured
    exp = _exposure()
    result = check_risk_limits(lim, exp, "crypto_onchain", 10.0)
    assert result.passed is True
    assert len(result.violations) == 0
    assert any("risk_limits.json not found" in w for w in result.warnings)


def test_check_passes_when_within_all_limits(tmp_path):
    lim = _limits(tmp_path)
    exp = _exposure(open_positions_count=2, total_exposure_usd=50.0, family_exposure_usd={"crypto_onchain": 20.0})
    result = check_risk_limits(lim, exp, "crypto_onchain", 10.0)
    assert result.passed is True
    assert result.violations == []


def test_check_max_open_positions_violation(tmp_path):
    lim = _limits(tmp_path, max_open_positions=3)
    exp = _exposure(open_positions_count=3)  # at the limit → violation
    result = check_risk_limits(lim, exp, "misc", 5.0)
    assert result.passed is False
    assert any("max_open_positions" in v for v in result.violations)


def test_check_daily_loss_circuit_breaker(tmp_path):
    lim = _limits(tmp_path, daily_loss_limit_usd=50.0)
    exp = _exposure(daily_pnl_usd=-55.0)
    result = check_risk_limits(lim, exp, "misc", 5.0)
    assert result.passed is False
    assert any("daily_loss_limit" in v for v in result.violations)


def test_check_daily_loss_not_triggered(tmp_path):
    lim = _limits(tmp_path, daily_loss_limit_usd=50.0)
    exp = _exposure(daily_pnl_usd=-30.0)
    result = check_risk_limits(lim, exp, "misc", 5.0)
    # -30 > -50 so not triggered
    assert not any("daily_loss_limit" in v for v in result.violations)


def test_check_total_exposure_violation(tmp_path):
    lim = _limits(tmp_path, max_exposure_total_usd=100.0)
    exp = _exposure(total_exposure_usd=95.0)
    # 95 + 10 = 105 > 100
    result = check_risk_limits(lim, exp, "misc", 10.0)
    assert result.passed is False
    assert any("max_exposure_total_usd" in v for v in result.violations)


def test_check_family_cap_violation(tmp_path):
    lim = _limits(tmp_path)  # crypto_onchain cap = 80
    exp = _exposure(family_exposure_usd={"crypto_onchain": 75.0})
    # 75 + 10 = 85 > 80
    result = check_risk_limits(lim, exp, "crypto_onchain", 10.0)
    assert result.passed is False
    assert any("crypto_onchain" in v for v in result.violations)


def test_check_position_size_violation(tmp_path):
    lim = _limits(tmp_path, max_position_size_usd=50.0)
    exp = _exposure(total_exposure_usd=0.0, family_exposure_usd={})
    result = check_risk_limits(lim, exp, "misc", 60.0)
    assert result.passed is False
    assert any("max_position_size_usd" in v for v in result.violations)


def test_check_multiple_violations(tmp_path):
    lim = _limits(tmp_path, max_open_positions=1, max_exposure_total_usd=50.0)
    exp = _exposure(open_positions_count=1, total_exposure_usd=45.0)
    # positions at limit AND exposure ceiling breached
    result = check_risk_limits(lim, exp, "misc", 10.0)
    assert result.passed is False
    assert len(result.violations) >= 2


# ---------------------------------------------------------------------------
# write_risk_check_artifact
# ---------------------------------------------------------------------------


def test_write_risk_check_artifact(tmp_path):
    lim = _limits(tmp_path)
    exp = _exposure()
    result = check_risk_limits(lim, exp, "misc", 5.0)
    output = tmp_path / "out" / "00-risk-check.json"
    write_risk_check_artifact(result, output)
    assert output.exists()
    data = json.loads(output.read_text())
    assert "passed" in data
    assert "violations" in data
    assert data["passed"] == result.passed


def test_write_risk_check_artifact_creates_parent_dirs(tmp_path):
    lim = _limits(tmp_path)
    exp = _exposure()
    result = check_risk_limits(lim, exp, "misc", 5.0)
    deep = tmp_path / "a" / "b" / "c" / "risk.json"
    write_risk_check_artifact(result, deep)
    assert deep.exists()
