"""Branchement à Interactive Brokers, par IB Gateway (ou TWS) qui tourne sur ton PC.

Réglages dans .env : IBKR_HOST (127.0.0.1), IBKR_PORT (4002 = compte papier d'IB Gateway, 4001 = vrai compte),
IBKR_CLIENT_ID (17). Il faut le paquet ib_async : pip install -e ".[ibkr]".

Les symboles suivent ceux de Yahoo dans toute l'app : RY.TO devient RY à la Bourse de Toronto (TSE), BRK-B devient
« BRK B » chez IBKR. Les ordres sont à cours limité (pour la journée seulement), jamais au marché.
"""

from __future__ import annotations

import asyncio
import os

from .vrai import Holding, Order, Snapshot

PAPER_PREFIXES = ("DU", "DF")  # les numéros des comptes papier d'IBKR commencent ainsi


def to_ib(ticker: str) -> tuple[str, str, str]:
    """Symbole Yahoo -> (symbole IBKR, bourse principale, devise)."""
    if ticker.endswith(".TO"):
        return ticker[:-3].replace("-", "."), "TSE", "CAD"
    if ticker.endswith(".V"):
        return ticker[:-2].replace("-", "."), "VENTURE", "CAD"
    return ticker.replace("-", " "), "", "USD"


def from_ib(symbol: str, exchange: str, currency: str) -> str:
    """Symbole IBKR -> symbole Yahoo (celui de l'arène)."""
    if currency == "CAD" or exchange in ("TSE", "VENTURE"):
        return symbol.replace(".", "-") + (".V" if exchange == "VENTURE" else ".TO")
    return symbol.replace(" ", "-")


class IBKRBroker:
    def __init__(self, host: str | None = None, port: int | None = None, client_id: int | None = None):
        try:
            asyncio.get_event_loop()
        except RuntimeError:  # dans le fil de la séance, il n'y a pas encore de boucle asyncio
            asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            from ib_async import IB
        except ImportError as e:
            raise RuntimeError("Il manque le paquet ib_async : lance  pip install -e \".[ibkr]\"") from e
        self.ib = IB()
        host = host or os.environ.get("IBKR_HOST", "127.0.0.1")
        port = port or int(os.environ.get("IBKR_PORT", "4002"))
        try:
            self.ib.connect(host, port, clientId=client_id or int(os.environ.get("IBKR_CLIENT_ID", "17")), timeout=15)
        except Exception as e:
            raise RuntimeError(f"Impossible de joindre IB Gateway sur {host}:{port}. Est-il ouvert et connecté, "
                               "avec l'API activée ?") from e
        self.account_id = self.ib.managedAccounts()[0]

    @property
    def paper(self) -> bool:
        return self.account_id.startswith(PAPER_PREFIXES)

    def snapshot(self) -> Snapshot:
        values = self.ib.accountValues(self.account_id)
        base = next((v.currency for v in values if v.tag == "NetLiquidation" and v.currency not in ("", "BASE")), "CAD")
        rates = {v.currency: float(v.value) for v in values if v.tag == "ExchangeRate" and v.currency}
        rates.setdefault(base, 1.0)
        cash = next((float(v.value) for v in values if v.tag == "TotalCashValue" and v.currency == base), 0.0)
        holdings = {}
        for item in self.ib.portfolio(self.account_id):
            c = item.contract
            if c.secType != "STK" or not item.position:
                continue
            ticker = from_ib(c.symbol, c.primaryExchange or c.exchange, c.currency)
            value = item.marketValue * rates.get(c.currency, 1.0) / rates[base]
            holdings[ticker] = Holding(ticker, float(item.position), value, c.currency)
        return Snapshot(self.account_id, cash, holdings, self.paper)

    def place(self, order: Order) -> tuple[str, str, str]:
        from ib_async import LimitOrder, Stock

        symbol, primary, currency = to_ib(order.ticker)
        contract = Stock(symbol, "SMART", currency, primaryExchange=primary)
        if not self.ib.qualifyContracts(contract):
            return "refusé", "", f"{order.ticker} introuvable chez IBKR"
        ib_order = LimitOrder("BUY" if order.side == "buy" else "SELL", order.qty, order.limit_price, tif="DAY",
                              account=self.account_id)
        trade = self.ib.placeOrder(contract, ib_order)
        self.ib.sleep(2)  # le temps que le courtier réponde
        status = trade.orderStatus.status or "envoyé"
        message = trade.log[-1].message if trade.log and trade.log[-1].message else ""
        return status, str(trade.order.orderId), message

    def close(self):
        self.ib.disconnect()

    def cancel_all(self) -> int:
        trades = [t for t in self.ib.openTrades() if t.order.account in ("", self.account_id)]
        for t in trades:
            self.ib.cancelOrder(t.order)
        if trades:
            self.ib.sleep(1)
        return len(trades)
