"""Commandes : chubby backtest | tick | serve | classement | journal | coach."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .arena import Arena
from .bots import all_bots, all_tickers
from .config import ArenaConfig
from .data import YFinanceData
from .store import Store

TORONTO = ZoneInfo("America/Toronto")
WARMUP_DAYS = 400  # historique dont les bots ont besoin (moyenne 200 jours, dividendes 12 mois)


def _load(start, end, cfg: ArenaConfig, cache: str, refresh: bool = False) -> YFinanceData:
    return YFinanceData.download(all_tickers(), pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS), end,
                                 cfg.calendar_ticker, cfg.fx_ticker, cache_dir=cache, refresh=refresh)


def _print_board(arena: Arena):
    db = arena.store.db
    span = db.execute("SELECT MIN(day) AS a, MAX(day) AS b, COUNT(DISTINCT day) AS n FROM snapshots").fetchone()
    if span["n"]:
        print(f"\nSaison du {span['a']} au {span['b']} ({span['n']} séances au total)")
    costs = {r["bot_id"]: (r["n"], r["fees"]) for r in db.execute(
        "SELECT bot_id, COUNT(*) AS n, SUM(fees_cad) AS fees FROM trades GROUP BY bot_id")}
    print(f"\n{'#':>2}  {'Chubby':<10} {'Capital':>12} {'Saison':>8} {'Pire baisse':>12} {'Sharpe':>7}"
          f" {'Trades':>7} {'Frais':>9} {'Score':>6}")
    for i, e in enumerate(arena.leaderboard(), 1):
        m = e["metrics"]
        n, fees = costs.get(e["id"], (0, 0.0))
        print(f"{i:>2}  {e['name']:<10} {e['equity']:>10,.0f} $ {m.total_return:>+8.1%} {m.max_drawdown:>12.1%}"
              f" {m.sharpe:>7.2f} {n:>7} {fees or 0:>7,.0f} $ {e['score']:>6}")
    print()


def cmd_backtest(args, cfg):
    data = _load(args.start, args.end, cfg, args.cache)
    arena = Arena(Store(args.db), data, all_bots(), cfg)
    n = arena.run(args.start, args.end)
    print(f"{n} nouvelle(s) séance(s) jouée(s) entre le {args.start} et le {args.end}.")
    _print_board(arena)


def cmd_tick(args, cfg):
    today = datetime.now(TORONTO).date()
    store = Store(args.db)
    data = _load(today - timedelta(days=10), today, cfg, args.cache, refresh=True)
    arena = Arena(store, data, all_bots(), cfg)
    # Premier lancement : l'arène commence aujourd'hui, sans rejouer le passé.
    start = arena.last_day + pd.Timedelta(days=1) if arena.last_day is not None else today
    n = arena.run(start, today)
    print(f"{n} nouvelle(s) séance(s) jouée(s).")
    _print_board(arena)


def cmd_serve(args, cfg):
    """Tourne en continu : une séance chaque jour de semaine à 16 h 20, heure de Toronto."""
    while True:
        now = datetime.now(TORONTO)
        run_at = now.replace(hour=16, minute=20, second=0, microsecond=0)
        if now >= run_at:
            run_at += timedelta(days=1)
        while run_at.weekday() >= 5:
            run_at += timedelta(days=1)
        logging.info("Prochaine séance : %s", run_at.isoformat())
        time.sleep((run_at - now).total_seconds())
        try:
            cmd_tick(args, cfg)
        except Exception:
            logging.exception("La séance a échoué, nouvel essai demain.")


def cmd_board(args, cfg):
    store = Store(args.db)
    _print_board(Arena(store, None, all_bots(), cfg))


def cmd_journal(args, cfg):
    rows = Store(args.db).db.execute("SELECT day, text FROM journal WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
                                     (args.bot, args.n)).fetchall()
    for r in reversed(rows):
        print(f"{r['day']}  {r['text']}")


def cmd_coach(args, cfg):
    store = Store(args.db)
    row = store.bot_row(args.bot)
    if row is None:
        raise SystemExit(f"Chubby inconnu : {args.bot}")
    factor = {"moins": 0.85, "plus": 1.15, "garde": 1.0}[args.conseil]
    value = max(0.4, min(2.0, row["aggressiveness"] * factor))
    store.set_aggressiveness(args.bot, value)
    store.add_xp(args.bot, 15)
    store.commit()
    print(f"{row['name']} : agressivité {row['aggressiveness']:.2f} -> {value:.2f} (+15 XP)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="chubby", description="Arène de bots en argent fictif.")
    p.add_argument("--db", default="data/arena.db", help="fichier SQLite de l'arène")
    p.add_argument("--cache", default="data/cache", help="dossier de cache des prix")
    p.add_argument("--compte", default="CELI", choices=["CELI", "REER", "NON_ENREGISTRE"])
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="rejouer une période passée")
    b.add_argument("--start", required=True)
    b.add_argument("--end", default=str(datetime.now(TORONTO).date()))
    b.set_defaults(fn=cmd_backtest)
    sub.add_parser("tick", help="jouer la ou les séances du jour").set_defaults(fn=cmd_tick)
    sub.add_parser("serve", help="tourner en continu (une séance par jour)").set_defaults(fn=cmd_serve)
    sub.add_parser("classement", help="afficher le classement").set_defaults(fn=cmd_board)
    j = sub.add_parser("journal", help="lire le journal d'un Chubby")
    j.add_argument("bot")
    j.add_argument("-n", type=int, default=15)
    j.set_defaults(fn=cmd_journal)
    c = sub.add_parser("coach", help="donner un conseil à un Chubby")
    c.add_argument("bot")
    c.add_argument("conseil", choices=["moins", "plus", "garde"])
    c.set_defaults(fn=cmd_coach)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose or args.cmd == "serve" else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    args.fn(args, ArenaConfig(account_type=args.compte))


if __name__ == "__main__":
    main()
