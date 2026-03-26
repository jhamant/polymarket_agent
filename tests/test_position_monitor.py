"""Tests for polymarket_agent.position_monitor (Gap 2)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from polymarket_agent.position_monitor import (
    _load_open_positions,
    _rule_based_recommendation,
    monitor_positions,
    write_monitor_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _perf_summary(tmp_path: Path, positions: list[dict]) -> Path:
    p = tmp_path / "latest_summary.json"
    p.write_text(json.dumps({"open_positions": positions}), encoding="utf-8")
    return p


def _pos(
    slug="test-market",
    side="YES",
    entry_price=0.60,
    size_shares=10.0,
    token_id="0xabc",
) -> dict:
    return {
        "market_slug": slug,
        "side": side,
        "entry_price": entry_price,
        "size_shares": size_shares,
        "token_id": token_id,
        "market_end_date": "2025-12-31",
        "question": "Will X happen?",
    }


# ---------------------------------------------------------------------------
# _load_open_positions
# ---------------------------------------------------------------------------


def test_load_open_positions_missing(tmp_path):
    positions = _load_open_positions(tmp_path / "none.json")
    assert positions == []


def test_load_open_positions_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{bad json}", encoding="utf-8")
    positions = _load_open_positions(p)
    assert positions == []


def test_load_open_positions_valid(tmp_path):
    p = _perf_summary(tmp_path, [_pos(), _pos(slug="other")])
    positions = _load_open_positions(p)
    assert len(positions) == 2


def test_load_open_positions_empty_list(tmp_path):
    p = _perf_summary(tmp_path, [])
    assert _load_open_positions(p) == []


# ---------------------------------------------------------------------------
# _rule_based_recommendation
# ---------------------------------------------------------------------------


def test_recommendation_hold_default():
    rec, reason = _rule_based_recommendation("YES", 0.60, 0.62, 0.033)
    assert rec == "hold"


def test_recommendation_close_near_resolution():
    # Price at 0.85 → near resolution for YES
    rec, reason = _rule_based_recommendation("YES", 0.60, 0.85, 0.41)
    assert rec == "close"
    assert "near resolution" in reason


def test_recommendation_close_near_resolution_no_side():
    # Price at 0.81 → triggers regardless of side
    rec, reason = _rule_based_recommendation("NO", 0.50, 0.81, 0.62)
    assert rec == "close"


def test_recommendation_close_loss_threshold():
    # 35% loss → triggers circuit
    rec, reason = _rule_based_recommendation("YES", 0.60, 0.39, -0.35)
    assert rec == "close"
    assert "threshold" in reason.lower()


def test_recommendation_no_close_loss_below_threshold():
    # 20% loss is below the -30% close threshold but above the 5% add threshold
    # → should recommend "add" (averaging down opportunity)
    rec, reason = _rule_based_recommendation("YES", 0.60, 0.48, -0.20)
    assert rec == "add"


def test_recommendation_add_when_price_dropped():
    # YES position: price dropped 10% below entry → averaging-down opportunity
    entry = 0.60
    current = entry * 0.88  # 12% drop
    rec, reason = _rule_based_recommendation("YES", entry, current, -0.12)
    assert rec == "add"
    assert "averaging-down" in reason.lower()


def test_recommendation_no_add_when_price_rose():
    # YES position: price rose — not an add opportunity
    rec, reason = _rule_based_recommendation("YES", 0.60, 0.65, 0.083)
    assert rec == "hold"


def test_recommendation_add_no_position():
    # NO position: current price fell 12% below entry → averaging-down opportunity
    entry_no = 0.40
    current_no = entry_no * 0.88  # dropped 12%
    rec, reason = _rule_based_recommendation("NO", entry_no, current_no, -0.12)
    assert rec == "add"
    assert "averaging-down" in reason.lower()


# ---------------------------------------------------------------------------
# monitor_positions
# ---------------------------------------------------------------------------


def test_monitor_positions_no_file(tmp_path):
    results = monitor_positions(tmp_path / "none.json", "https://example.com")
    assert results == []


def test_monitor_positions_empty(tmp_path):
    p = _perf_summary(tmp_path, [])
    results = monitor_positions(p, "https://example.com")
    assert results == []


def test_monitor_positions_with_mocked_price(tmp_path):
    p = _perf_summary(tmp_path, [_pos(entry_price=0.60)])

    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.65):
        results = monitor_positions(p, "https://example.com")

    assert len(results) == 1
    r = results[0]
    assert r.current_price == 0.65
    assert r.entry_price == 0.60
    assert r.unrealized_pnl_usd == pytest.approx((0.65 - 0.60) * 10.0, rel=1e-6)
    assert r.recommendation == "hold"


def test_monitor_positions_price_fallback_on_api_failure(tmp_path):
    p = _perf_summary(tmp_path, [_pos(entry_price=0.55)])

    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=None):
        results = monitor_positions(p, "https://example.com")

    # Falls back to entry price — no PnL movement
    assert results[0].current_price == 0.55
    assert results[0].unrealized_pnl_usd == pytest.approx(0.0, abs=1e-9)


def test_monitor_positions_no_side_close_near_resolution(tmp_path):
    p = _perf_summary(tmp_path, [_pos(entry_price=0.70)])

    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.82):
        results = monitor_positions(p, "https://example.com")

    assert results[0].recommendation == "close"


def test_monitor_positions_daily_loss_triggers_close(tmp_path):
    p = _perf_summary(tmp_path, [_pos(entry_price=0.60, size_shares=100.0)])

    # Simulate a 40% drop → triggers loss circuit
    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.36):
        results = monitor_positions(p, "https://example.com")

    assert results[0].recommendation == "close"


def test_monitor_positions_no_position_side(tmp_path):
    # NO position — YES price rises → NO value drops
    pos = _pos(side="NO", entry_price=0.40)
    p = _perf_summary(tmp_path, [pos])

    # YES price = 0.65 → NO price = 0.35 (dropped 12.5% from NO entry of 0.40)
    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.65):
        results = monitor_positions(p, "https://example.com")

    r = results[0]
    assert r.current_price == pytest.approx(0.35, rel=1e-6)  # 1 - 0.65
    assert r.unrealized_pnl_usd < 0  # loss on NO position


# ---------------------------------------------------------------------------
# write_monitor_result
# ---------------------------------------------------------------------------


def test_write_monitor_result_creates_file(tmp_path):
    p = _perf_summary(tmp_path, [_pos()])
    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.60):
        positions = monitor_positions(p, "https://example.com")

    out = tmp_path / "out" / "monitor.json"
    write_monitor_result(positions, out, also_write_latest=False)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["open_positions_count"] == 1


def test_write_monitor_result_latest_updated(tmp_path):
    p = _perf_summary(tmp_path, [_pos()])
    with patch("polymarket_agent.position_monitor._fetch_current_price", return_value=0.60):
        positions = monitor_positions(p, "https://example.com")

    out = tmp_path / "monitor.json"
    write_monitor_result(positions, out, also_write_latest=True)
    latest = tmp_path / "position_monitor_latest.json"
    assert latest.exists()


def test_write_monitor_result_summary_counts(tmp_path):
    # Two positions: one hold, one close (near resolution)
    positions_data = [
        _pos(slug="hold-market", entry_price=0.50),
        _pos(slug="close-market", entry_price=0.70),
    ]
    p = _perf_summary(tmp_path, positions_data)

    def fake_price(_, slug):
        return 0.55 if slug == "hold-market" else 0.82

    with patch("polymarket_agent.position_monitor._fetch_current_price", side_effect=fake_price):
        positions = monitor_positions(p, "https://example.com")

    out = tmp_path / "monitor.json"
    payload = write_monitor_result(positions, out, also_write_latest=False)
    assert payload["summary"]["hold"] == 1
    assert payload["summary"]["close"] == 1
