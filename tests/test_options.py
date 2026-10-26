import pandas as pd
import pytest

from chubby import options as opt
from chubby.broker import Account, PaperBroker
from chubby.config import ArenaConfig, currency_of
from chubby.data import FrameData


def market(closes):
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    frame = pd.DataFrame({"Open": closes, "Close": closes, "Dividends": 0.0}, index=idx)
    return opt.OptionData(FrameData({"QQQ": frame, "XIC.TO": frame}, pd.Series(1.35, index=idx), "XIC.TO")), idx


def test_black_scholes_basics():
    call = opt.black_scholes(100, 100, 0.25, 0.2, "C")
    put = opt.black_scholes(100, 100, 0.25, 0.2, "P")
    assert 3 < call < 6
    assert call - put == pytest.approx(100 - 100 * 2.718281828 ** (-0.04 * 0.25), rel=1e-3)  # parité call-put
    assert opt.black_scholes(110, 100, 0, 0.2, "C") == 10  # à l'échéance : valeur intrinsèque


def test_symbols_expiry_and_currency():
    sym = opt.symbol("QQQ", "C", 480, "2024-03-15")
    assert opt.parse(sym) == ("QQQ", "C", 480.0, pd.Timestamp("2024-03-15"))
    assert currency_of(sym) == "USD"
    exp = opt.pick_expiry("2024-01-10", 40)
    assert exp.weekday() == 4 and 15 <= exp.day <= 21 and (exp - pd.Timestamp("2024-01-10")).days >= 21
    assert opt.pick_strike(452, 0.05, "C") == 475


def test_option_trade_and_expiry_settlement():
    closes = [100.0] * 30 + [112.0] * 30
    data, idx = market(closes)
    broker = PaperBroker(ArenaConfig(slippage_bps=0, usd_account=True), data)
    acct = Account("x", 10_000)
    sym = opt.symbol("QQQ", "C", 105, idx[40])
    fill = broker.buy(acct, sym, 3, idx[25])
    assert fill and acct.positions[sym].qty == 3
    # 100 actions par contrat, plus 0,75 $ de frais par contrat
    assert fill.value_cad == pytest.approx(fill.price * 100 * 3 * 1.35 + 0.75 * 3, rel=1e-6)
    assert broker.settle_expired(acct, idx[39]) == []  # pas encore l'échéance
    [settled] = broker.settle_expired(acct, idx[40])
    assert settled.side == "expire" and sym not in acct.positions
    assert settled.value_cad == pytest.approx(3 * (112 - 105) * 100 * 1.35)


def test_cash_secured_put_assignment_then_covered_call():
    closes = [50.0] * 30 + [44.0] * 30 + [60.0] * 40
    data, idx = market(closes)
    broker = PaperBroker(ArenaConfig(slippage_bps=0, usd_account=True), data)
    acct = Account("x", 10_000)
    put = opt.symbol("QQQ", "P", 48, idx[40])
    assert broker.trade(acct, put, -2, idx[25]) is None  # 2 × 48 × 100 × 1,35 > 10 000 $ : non garanti
    fill = broker.trade(acct, put, -1, idx[25])
    assert fill and acct.positions[put].qty == -1 and acct.cash > 10_000  # prime encaissée
    assert broker.free_cash(acct, idx[25]) == pytest.approx(acct.cash - 48 * 100 * 1.35)
    [assigned] = broker.settle_expired(acct, idx[40])  # QQQ à 44 < 48 : exercé
    assert assigned.side == "assign" and acct.positions["QQQ"].qty == 100
    call = opt.symbol("QQQ", "C", 55, idx[70])
    assert broker.trade(acct, opt.symbol("QQQ", "C", 55, idx[70]), -2, idx[45]) is None  # seulement 100 actions
    assert broker.trade(acct, call, -1, idx[45])
    cash_before = acct.cash
    [called] = broker.settle_expired(acct, idx[70])  # QQQ à 60 > 55 : actions reprises à 55
    assert called.side == "assign" and "QQQ" not in acct.positions
    assert acct.cash == pytest.approx(cash_before + 100 * 55 * 1.35)


def test_buy_back_short_option_and_expire_worthless():
    closes = [50.0] * 60
    data, idx = market(closes)
    broker = PaperBroker(ArenaConfig(slippage_bps=0, usd_account=True), data)
    acct = Account("x", 10_000)
    put = opt.symbol("QQQ", "P", 45, idx[50])
    broker.trade(acct, put, -1, idx[10])
    assert broker.trade(acct, put, 1, idx[20]).side == "buy" and put not in acct.positions
    broker.trade(acct, put, -1, idx[30])
    [expired] = broker.settle_expired(acct, idx[50])
    assert expired.side == "expire" and put not in acct.positions
