"""Tests for polymarket_agent.structural_alpha (Gap 4)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polymarket_agent.structural_alpha import (
    _extract_legs,
    _parse_structural_alpha_json,
    execute_multi_leg,
    write_multi_leg_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_alpha_json(tmp_path: Path, legs: list[dict] | None = None, **extras) -> Path:
    if legs is None:
        legs = [
            {"market_slug": "market-a", "token_id": "0xaaa", "side": "YES", "size": 10.0, "price": 0.55},
            {"market_slug": "market-b", "token_id": "0xbbb", "side": "NO", "size": 10.0, "price": 0.40},
        ]
    data = {"alpha_type": "correlated_spread", "legs": legs, **extras}
    p = tmp_path / "06-structural-alpha.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _filled_response(order_id: str, fill_price: float, size: float = 10.0) -> dict:
    return {"orderID": order_id, "status": "filled", "fillPrice": fill_price, "fillSize": size}


# ---------------------------------------------------------------------------
# _parse_structural_alpha_json
# ---------------------------------------------------------------------------


def test_parse_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _parse_structural_alpha_json(tmp_path / "none.json")


def test_parse_valid_file(tmp_path):
    p = _write_alpha_json(tmp_path)
    raw = _parse_structural_alpha_json(p)
    assert "legs" in raw
    assert len(raw["legs"]) == 2


# ---------------------------------------------------------------------------
# _extract_legs
# ---------------------------------------------------------------------------


def test_extract_legs_valid():
    raw = {"legs": [{"market_slug": "a", "token_id": "0x1", "side": "YES", "size": 5.0, "price": 0.5}]}
    legs = _extract_legs(raw)
    assert len(legs) == 1


def test_extract_legs_empty_raises():
    with pytest.raises(ValueError, match="No legs found"):
        _extract_legs({"legs": []})


def test_extract_legs_missing_key_raises():
    with pytest.raises(ValueError):
        _extract_legs({})


# ---------------------------------------------------------------------------
# execute_multi_leg — dry-run
# ---------------------------------------------------------------------------


def test_dry_run_two_legs(tmp_path):
    p = _write_alpha_json(tmp_path)
    result = execute_multi_leg(p, live_trading=False)
    assert result.dry_run is True
    assert result.status == "dry_run"
    assert result.total_legs == 2
    assert len(result.legs) == 2
    assert all(leg.status == "dry_run" for leg in result.legs)
    assert result.legs_submitted == 0


def test_dry_run_missing_token_still_dry(tmp_path):
    legs = [{"market_slug": "a", "side": "YES", "size": 5.0, "price": 0.5}]  # no token_id
    p = _write_alpha_json(tmp_path, legs=legs)
    result = execute_multi_leg(p, live_trading=False)
    # In dry-run mode, we don't validate token_id — just return dry_run status
    assert result.dry_run is True
    assert result.legs[0].status == "dry_run"


def test_dry_run_missing_file(tmp_path):
    result = execute_multi_leg(tmp_path / "none.json", live_trading=False)
    assert result.status == "failed"
    assert result.total_legs == 0


def test_dry_run_no_legs(tmp_path):
    p = _write_alpha_json(tmp_path, legs=[])
    result = execute_multi_leg(p, live_trading=False)
    assert result.status == "failed"
    assert "No legs" in (result.error or "")


# ---------------------------------------------------------------------------
# execute_multi_leg — live mode
# ---------------------------------------------------------------------------


def test_live_all_legs_filled(tmp_path):
    p = _write_alpha_json(tmp_path)
    with (
        patch("polymarket_agent.structural_alpha._build_clob_client") as mock_build,
        patch("polymarket_agent.structural_alpha._submit_limit_order") as mock_submit,
        patch("polymarket_agent.structural_alpha._poll_order_fill") as mock_poll,
    ):
        mock_build.return_value = MagicMock()
        mock_submit.side_effect = [
            {"orderID": "order-1", "status": "open"},
            {"orderID": "order-2", "status": "open"},
        ]
        mock_poll.side_effect = [
            {"status": "filled", "fillPrice": 0.56, "fillSize": 10.0},
            {"status": "filled", "fillPrice": 0.41, "fillSize": 10.0},
        ]
        result = execute_multi_leg(p, live_trading=True)

    assert result.status == "completed"
    assert result.legs_filled == 2
    assert result.legs_failed == 0
    assert result.partial is False
    assert result.legs[0].slippage_pct == pytest.approx(abs(0.56 - 0.55) / 0.55 * 100, rel=1e-3)


def test_live_first_leg_fails_safe_stop(tmp_path):
    p = _write_alpha_json(tmp_path)
    with (
        patch("polymarket_agent.structural_alpha._build_clob_client") as mock_build,
        patch("polymarket_agent.structural_alpha._submit_limit_order") as mock_submit,
        patch("polymarket_agent.structural_alpha._poll_order_fill"),
    ):
        mock_build.return_value = MagicMock()
        mock_submit.side_effect = RuntimeError("CLOB error")
        result = execute_multi_leg(p, live_trading=True)

    assert result.legs_failed == 1
    assert result.legs_skipped == 1
    assert result.status == "failed"  # no fills, so not "partial"
    assert result.legs[1].status == "skipped"
    assert "safe-stop" in (result.legs[1].error or "").lower()


def test_live_second_leg_fails_partial(tmp_path):
    p = _write_alpha_json(tmp_path)
    with (
        patch("polymarket_agent.structural_alpha._build_clob_client") as mock_build,
        patch("polymarket_agent.structural_alpha._submit_limit_order") as mock_submit,
        patch("polymarket_agent.structural_alpha._poll_order_fill") as mock_poll,
    ):
        mock_build.return_value = MagicMock()
        mock_submit.side_effect = [
            {"orderID": "order-1", "status": "open"},
            RuntimeError("second leg failed"),
        ]
        mock_poll.return_value = {"status": "filled", "fillPrice": 0.56, "fillSize": 10.0}
        result = execute_multi_leg(p, live_trading=True)

    assert result.legs_filled == 1
    assert result.legs_failed == 1
    assert result.partial is True
    assert result.status == "partial"


def test_live_client_build_fails(tmp_path):
    p = _write_alpha_json(tmp_path)
    with patch("polymarket_agent.structural_alpha._build_clob_client", side_effect=EnvironmentError("no private key")):
        result = execute_multi_leg(p, live_trading=True)

    assert result.status == "failed"
    assert "no private key" in (result.error or "")


def test_live_missing_token_id(tmp_path):
    legs = [
        {"market_slug": "a", "side": "YES", "size": 5.0, "price": 0.5},  # no token_id
    ]
    p = _write_alpha_json(tmp_path, legs=legs)
    with patch("polymarket_agent.structural_alpha._build_clob_client") as mock_build:
        mock_build.return_value = MagicMock()
        result = execute_multi_leg(p, live_trading=True)

    assert result.legs[0].status == "error"
    assert "token_id" in (result.legs[0].error or "")


# ---------------------------------------------------------------------------
# write_multi_leg_result
# ---------------------------------------------------------------------------


def test_write_multi_leg_result(tmp_path):
    p = _write_alpha_json(tmp_path)
    result = execute_multi_leg(p, live_trading=False)
    out = tmp_path / "out" / "multi-leg.json"
    write_multi_leg_result(result, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["dry_run"] is True
    assert data["total_legs"] == 2
    assert len(data["legs"]) == 2


def test_write_multi_leg_result_creates_dirs(tmp_path):
    p = _write_alpha_json(tmp_path)
    result = execute_multi_leg(p, live_trading=False)
    deep = tmp_path / "a" / "b" / "result.json"
    write_multi_leg_result(result, deep)
    assert deep.exists()


def test_multi_leg_result_as_dict_complete(tmp_path):
    p = _write_alpha_json(tmp_path)
    result = execute_multi_leg(p, live_trading=False)
    d = result.as_dict()
    for key in (
        "dry_run",
        "total_legs",
        "legs_submitted",
        "legs_filled",
        "legs_failed",
        "legs_skipped",
        "partial",
        "status",
        "legs",
    ):
        assert key in d, f"Missing key in as_dict(): {key}"
