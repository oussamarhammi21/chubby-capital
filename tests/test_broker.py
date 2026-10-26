import pandas as pd
import pytest

from chubby.broker import Account, PaperBroker
from chubby.config import ArenaConfig
from chubby.data import FrameData

DAY = pd.Timestamp("2024-03-01")


def market(div_usd: float = 0.0) -> FrameData:
    idx = pd.DatetimeIndex([DAY])
    frames = {
        "RY.TO": pd.DataFrame({"Open": [100.0], "Close": [110.0], "Dividends": [0.0]}, index=idx),
        "KO": pd.DataFrame({"Open": [50.0], "Close": [50.0], "Dividends": [div_usd]}, index=idx),
        "XIC.TO": pd.DataFrame({"Open": [30.0], "Close": [30.0], "Dividends": [0.0]}, index=idx),
    }
    return FrameData(frames, pd.Series([1.4], index=idx), "XIC.TO")


def test_buy_canadian_stock_charges_slippage_only():
    cfg = ArenaConfig(slippage_bps=10)
    broker, acct = PaperBroker(cfg, market()), Account("x", 10_000)
    fill = broker.buy(acct, "RY.TO", 10, DAY)
    assert fill.value_cad == pytest.approx(10 * 100 * 1.001)
    assert acct.cash == pytest.approx(10_000 - 1001)
    assert acct.positions["RY.TO"].qty == 10


def test_buy_us_stock_converts_currency_and_charges_fx_fee():
    cfg = ArenaConfig(slippage_bps=0, fx_fee=0.015, usd_account=False)
    broker, acct = PaperBroker(cfg, market()), Account("x", 10_000)
    fill = broker.buy(acct, "KO", 10, DAY)
    assert fill.value_cad == pytest.approx(10 * 50 * 1.4 * 1.015)
    assert fill.fees_cad == pytest.approx(10 * 50 * 1.4 * 0.015)


def test_cannot_buy_more_than_cash():
    broker, acct = PaperBroker(ArenaConfig(), market()), Account("x", 500)
    assert broker.buy(acct, "RY.TO", 10, DAY) is None
    assert acct.cash == 500


def test_rebalance_reaches_targets_without_going_negative():
    broker, acct = PaperBroker(ArenaConfig(), market()), Account("x", 10_000)
    broker.rebalance(acct, {"RY.TO": 0.5, "KO": 0.45}, DAY)
    assert acct.cash >= 0
    values = broker.value_of(acct, DAY, "Open")
    assert values["RY.TO"] == pytest.approx(5_000, rel=0.03)
    assert values["KO"] == pytest.approx(4_500, rel=0.03)
    broker.rebalance(acct, {}, DAY)
    assert acct.positions == {}


def test_us_dividend_withholding_in_celi_but_not_reer():
    for account_type, expected in (("CELI", 10 * 1.0 * 1.4 * 0.85 * 0.985), ("REER", 10 * 1.0 * 1.4 * 0.985)):
        broker = PaperBroker(ArenaConfig(account_type=account_type, slippage_bps=0, usd_account=False),
                             market(div_usd=1.0))
        acct = Account("x", 10_000)
        broker.buy(acct, "KO", 10, DAY)
        cash = acct.cash
        [(ticker, amount)] = broker.credit_dividends(acct, DAY)
        assert ticker == "KO" and amount == pytest.approx(expected)
        assert acct.cash == pytest.approx(cash + expected)


def test_usd_account_trades_and_dividends_without_conversion_fee():
    broker = PaperBroker(ArenaConfig(slippage_bps=0, usd_account=True), market(div_usd=1.0))
    acct = Account("x", 10_000)
    fill = broker.buy(acct, "KO", 10, DAY)
    assert fill.fees_cad == 0
    assert fill.value_cad == pytest.approx(10 * 50 * 1.4)
    [(_, amount)] = broker.credit_dividends(acct, DAY)
    assert amount == pytest.approx(10 * 1.0 * 1.4 * 0.85)  # seulement la retenue américaine
