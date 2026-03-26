"""Tests for polymarket_agent.place_order (Gap 1 + Gap 3)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polymarket_agent.place_order import (
    _extract_order_params,
    _parse_execution_json,
    place_order,
    write_order_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_execution_json(tmp_path: Path, **overrides) -> Path:
    defaults = {
        "execution_ready": "yes",
        "order_plan": {
            "side": "YES",
            "token_id": "0xabc123",
            "size": 10.0,
            "price_or_price_band": {"limit": 0.62},
        },
    }
    defaults.update(overrides)
    p = tmp_path / "11-execution.json"
    p.write_text(json.dumps(defaults), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _parse_execution_json
# ---------------------------------------------------------------------------


def test_parse_execution_json_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _parse_execution_json(tmp_path / "nonexistent.json")


def test_parse_execution_json_valid(tmp_path):
    p = _write_execution_json(tmp_path)
    raw = _parse_execution_json(p)
    assert raw["execution_ready"] == "yes"


# ---------------------------------------------------------------------------
# _extract_order_params
# ---------------------------------------------------------------------------


def test_extract_order_params_valid():
    raw = {
        "order_plan": {
            "side": "YES",
            "token_id": "0xabc",
            "size": 10.0,
            "price_or_price_band": {"limit": 0.62},
        }
    }
    side, token_id, size, price = _extract_order_params(raw)
    assert side == "YES"
    assert token_id == "0xabc"
    assert size == 10.0
    assert price == 0.62


def test_extract_order_params_no_side_raises():
    raw = {"order_plan": {"token_id": "0xabc", "size": 5.0, "price_or_price_band": 0.5}}
    with pytest.raises(ValueError, match="Invalid order side"):
        _extract_order_params(raw)


def test_extract_order_params_no_token_raises():
    raw = {"order_plan": {"side": "NO", "size": 5.0, "price_or_price_band": 0.4}}
    with pytest.raises(ValueError, match="token_id is missing"):
        _extract_order_params(raw)


def test_extract_order_params_zero_size_raises():
    raw = {"order_plan": {"side": "YES", "token_id": "0xabc", "size": 0.0, "price_or_price_band": 0.5}}
    with pytest.raises(ValueError, match="size must be positive"):
        _extract_order_params(raw)


def test_extract_order_params_out_of_range_price():
    raw = {"order_plan": {"side": "YES", "token_id": "0xabc", "size": 10.0, "price_or_price_band": 1.5}}
    with pytest.raises(ValueError, match="out of valid range"):
        _extract_order_params(raw)


def test_extract_order_params_scalar_price():
    raw = {"order_plan": {"side": "NO", "token_id": "0xdef", "size": 5.0, "price_or_price_band": 0.38}}
    side, token_id, size, price = _extract_order_params(raw)
    assert side == "NO"
    assert price == 0.38


# ---------------------------------------------------------------------------
# place_order — dry-run mode
# ---------------------------------------------------------------------------


def test_place_order_dry_run_execution_ready(tmp_path):
    p = _write_execution_json(tmp_path)
    result = place_order(p, live_trading=False)
    assert result.dry_run is True
    assert result.status == "dry_run"
    assert result.submitted is False
    assert result.side == "YES"
    assert result.size == 10.0
    assert result.price == 0.62


def test_place_order_dry_run_not_ready(tmp_path):
    p = _write_execution_json(tmp_path, execution_ready="no")
    result = place_order(p, live_trading=False)
    assert result.status == "rejected"
    assert result.execution_ready is False
    assert result.submitted is False


def test_place_order_dry_run_missing_token(tmp_path):
    p = tmp_path / "11-execution.json"
    p.write_text(
        json.dumps(
            {
                "execution_ready": "yes",
                "order_plan": {"side": "YES", "size": 5.0, "price_or_price_band": 0.5},
            }
        ),
        encoding="utf-8",
    )
    result = place_order(p, live_trading=False)
    assert result.status == "error"
    assert "token_id" in (result.error or "")


def test_place_order_dry_run_invalid_price(tmp_path):
    p = _write_execution_json(tmp_path)
    # Override price to be out of range
    data = json.loads(p.read_text())
    data["order_plan"]["price_or_price_band"] = {"limit": 1.5}
    p.write_text(json.dumps(data))
    result = place_order(p, live_trading=False)
    assert result.status == "error"


# ---------------------------------------------------------------------------
# place_order — live mode (mocked CLOB client)
# ---------------------------------------------------------------------------


def _mock_client(order_id="order-42", status="filled", fill_price=0.63, fill_size=10.0):
    client = MagicMock()
    client.post_order.return_value = {"orderID": order_id, "status": status}
    client.get_order.return_value = {
        "order_id": order_id,
        "status": status,
        "fillPrice": fill_price,
        "fillSize": fill_size,
    }
    return client


def test_place_order_live_success(tmp_path):
    p = _write_execution_json(tmp_path)
    client = _mock_client()

    with (
        patch("polymarket_agent.place_order._build_clob_client", return_value=client),
        patch(
            "polymarket_agent.place_order._submit_limit_order", return_value={"orderID": "order-42", "status": "open"}
        ),
        patch(
            "polymarket_agent.place_order._poll_order_fill",
            return_value={"status": "filled", "fillPrice": 0.63, "fillSize": 10.0},
        ),
    ):
        result = place_order(p, live_trading=True)

    assert result.dry_run is False
    assert result.submitted is True
    assert result.order_id == "order-42"
    assert result.fill_price == 0.63
    assert result.slippage_pct == pytest.approx(abs(0.63 - 0.62) / 0.62 * 100, rel=1e-3)


def test_place_order_live_client_error(tmp_path):
    p = _write_execution_json(tmp_path)
    with patch("polymarket_agent.place_order._build_clob_client", side_effect=EnvironmentError("no key")):
        result = place_order(p, live_trading=True)
    assert result.status == "error"
    assert "no key" in (result.error or "")


def test_place_order_live_submission_error(tmp_path):
    p = _write_execution_json(tmp_path)
    client = MagicMock()
    with (
        patch("polymarket_agent.place_order._build_clob_client", return_value=client),
        patch("polymarket_agent.place_order._submit_limit_order", side_effect=RuntimeError("CLOB down")),
    ):
        result = place_order(p, live_trading=True)
    assert result.status == "error"
    assert "CLOB down" in (result.error or "")


# ---------------------------------------------------------------------------
# write_order_result
# ---------------------------------------------------------------------------


def test_write_order_result(tmp_path):
    p = _write_execution_json(tmp_path)
    result = place_order(p, live_trading=False)
    out = tmp_path / "fill" / "11-execution-fill.json"
    write_order_result(result, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["status"] == "dry_run"
    assert data["dry_run"] is True


def test_write_order_result_creates_dirs(tmp_path):
    p = _write_execution_json(tmp_path)
    result = place_order(p, live_trading=False)
    deep = tmp_path / "a" / "b" / "fill.json"
    write_order_result(result, deep)
    assert deep.exists()
