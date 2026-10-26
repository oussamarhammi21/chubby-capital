"""Règles de l'arène : argent de départ, frais, devises."""

from dataclasses import dataclass

CAD_SUFFIXES = (".TO", ".V", ".NE", ".CN")


def currency_of(ticker: str) -> str:
    """Les titres cotés au Canada (RY.TO, ...) sont en CAD, le reste en USD. Une option suit son sous-jacent."""
    if ticker.startswith("OPT:"):
        ticker = ticker.split(":")[1]
    return "CAD" if ticker.upper().endswith(CAD_SUFFIXES) else "USD"


@dataclass(frozen=True)
class ArenaConfig:
    starting_cash: float = 10_000.0  # en CAD
    account_type: str = "CELI"  # CELI, REER ou NON_ENREGISTRE
    slippage_bps: float = 5.0  # écart défavorable appliqué à chaque exécution
    fx_fee: float = 0.015  # frais de conversion CAD <-> USD (1,5 % comme Wealthsimple)
    usd_account: bool = True  # compte en dollars américains : pas de conversion à chaque trade
    usd_account_monthly_fee: float = 10.0  # prix du forfait qui donne le compte en USD, en CAD
    commission: float = 0.0  # par ordre, en CAD
    option_slippage: float = 0.02  # l'écart achat-vente des options est large : 2 % de la prime
    option_fee_per_contract: float = 0.75  # frais par contrat d'option, à ajuster selon ton courtier
    min_trade_cad: float = 100.0  # on ignore les ajustements plus petits
    us_withholding: float = 0.15  # retenue sur les dividendes américains
    calendar_ticker: str = "XIC.TO"  # définit les jours de bourse de l'arène
    fx_ticker: str = "CAD=X"  # cours USD -> CAD sur Yahoo Finance

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 10_000

    @property
    def trade_fx_fee(self) -> float:
        """Frais de conversion payés sur chaque trade ou dividende américain."""
        return 0.0 if self.usd_account else self.fx_fee

    @property
    def monthly_fee(self) -> float:
        return self.usd_account_monthly_fee if self.usd_account else 0.0

    @property
    def withholding(self) -> float:
        # Le REER est exempté de la retenue américaine par la convention fiscale.
        return 0.0 if self.account_type.upper() == "REER" else self.us_withholding
