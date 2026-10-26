"""Commandes : chubby backtest | tick | serve | classement | journal | coach | coach-ia | conseil | web."""

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
WARMUP_DAYS = 800  # historique dont les bots ont besoin (Matcha apprend sur deux ans)


def _load(start, end, cfg: ArenaConfig, cache: str, refresh: bool = False) -> YFinanceData:
    return YFinanceData.download(all_tickers(), pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS), end,
                                 cfg.calendar_ticker, cfg.fx_ticker, cache_dir=cache, refresh=refresh)


def _print_board(arena: Arena):
    db = arena.store.db
    span = db.execute("SELECT MIN(day) AS a, MAX(day) AS b, COUNT(DISTINCT day) AS n FROM snapshots").fetchone()
    if span["n"]:
        print(f"\nSaison du {span['a']} au {span['b']} ({span['n']} séances au total)")
    costs = {r["bot_id"]: (r["n"], r["fees"] or 0.0) for r in db.execute(
        "SELECT bot_id, COUNT(*) AS n, SUM(fees_cad) AS fees FROM trades GROUP BY bot_id")}
    for r in db.execute("SELECT bot_id, SUM(json_extract(payload, '$.amount_cad')) AS fees"
                        " FROM events WHERE kind = 'fee' GROUP BY bot_id"):
        n, fees = costs.get(r["bot_id"], (0, 0.0))
        costs[r["bot_id"]] = (n, fees + r["fees"])  # le forfait mensuel compte aussi
    print(f"\n{'#':>2}  {'Chubby':<10} {'Capital':>12} {'Saison':>8} {'Pire baisse':>12} {'Sharpe':>7}"
          f" {'Trades':>7} {'Frais':>9} {'Score':>6}")
    for i, e in enumerate(arena.leaderboard(), 1):
        m = e["metrics"]
        n, fees = costs.get(e["id"], (0, 0.0))
        print(f"{i:>2}  {e['name']:<10} {e['equity']:>10,.0f} $ {m.total_return:>+8.1%} {m.max_drawdown:>12.1%}"
              f" {m.sharpe:>7.2f} {n:>7} {fees:>7,.0f} $ {e['score']:>6}")
    print()


def _progress(done: int, total: int, day):
    if done == total or done % 20 == 0:
        print(f"\r  séance {done}/{total} ({day.date()})", end="\n" if done == total else "", flush=True)


def cmd_backtest(args, cfg):
    print("Chargement des prix…", flush=True)
    data = _load(args.start, args.end, cfg, args.cache)
    arena = Arena(Store(args.db), data, all_bots(), cfg)
    if arena.last_day is not None:
        print(f"Reprise après le {arena.last_day.date()} (séances déjà jouées gardées).")
    n = arena.run(args.start, args.end, progress=_progress)
    print(f"{n} nouvelle(s) séance(s) jouée(s) entre le {args.start} et le {args.end}.")
    _print_board(arena)


def cmd_tick(args, cfg):
    today = datetime.now(TORONTO).date()
    store = Store(args.db)
    data = _load(today - timedelta(days=10), today, cfg, args.cache, refresh=True)
    arena = Arena(store, data, all_bots(ia=not args.sans_ia), cfg)
    # Premier lancement : l'arène commence aujourd'hui, sans rejouer le passé.
    start = arena.last_day + pd.Timedelta(days=1) if arena.last_day is not None else today
    n = arena.run(start, today)
    print(f"{n} nouvelle(s) séance(s) jouée(s).")
    if n and not args.sans_ia and arena.last_day.weekday() == 4:  # le vendredi, revue de la semaine
        _weekly_review(arena)
    _print_board(arena)


def _weekly_review(arena: Arena):
    from .ai_coach import weekly_review
    try:
        n = weekly_review(arena.store, arena.data, arena.bots, arena.last_day, starting_cash=arena.cfg.starting_cash)
        print(f"Coach Claude : {n} Chubby(s) IA coaché(s).")
    except Exception as e:  # pas de clé, pas de réseau : l'arène continue sans coaching
        logging.warning("Coaching IA impossible cette semaine : %s", e)


def cmd_coach_ia(args, cfg):
    """Lance tout de suite la revue hebdomadaire des Chubbies IA."""
    store = Store(args.db)
    last = store.get_meta("last_day")
    if last is None:
        raise SystemExit("L'arène n'a encore joué aucune séance.")
    data = _load(pd.Timestamp(last) - pd.Timedelta(days=10), last, cfg, args.cache)
    arena = Arena(store, data, all_bots(ia=True), cfg)
    _weekly_review(arena)


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
    _print_board(Arena(store, None, all_bots(ia=True), cfg))


def cmd_journal(args, cfg):
    rows = Store(args.db).db.execute("SELECT day, text FROM journal WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
                                     (args.bot, args.n)).fetchall()
    for r in reversed(rows):
        print(f"{r['day']}  {r['text']}")


def cmd_coach(args, cfg):
    from .coaching import coach
    result = coach(Store(args.db), args.bot, args.conseil)
    if result is None:
        raise SystemExit(f"Chubby inconnu : {args.bot}")
    print(f"{result['name']} : agressivité {result['old']:.2f} -> {result['aggressiveness']:.2f} (+15 XP)")


def cmd_conseil(args, cfg):
    from .web.api import advice
    text = open(args.fichier, encoding="utf-8-sig").read() if args.fichier else ""
    profile = {"risk": args.risque, "style": args.style, "horizon": args.horizon, "usd_account": args.compte_usd}
    out = advice(Store(args.db), cfg, profile, text)
    print(f"\nTon portefeuille : {out['total_cad']:,.0f} $ CAD")
    print("\nLes Chubbies qui te ressemblent :")
    for m in out["matches"]:
        status = "certifié" if m["certified"] else f"en observation ({m['sessions']} séances)"
        print(f"  {m['name']:<14} affinité {m['fit']:>3}  score {m['score']:>3}  saison {m['return']:+.1%}  {status}")
    if "advisor" in out:
        print(f"\nPour ressembler à {out['advisor']['name']} :")
        for r in out["rows"]:
            if r["action"] != "garder":
                print(f"  {r['action']:<8} {r['symbol']:<12} toi {r['mine']:>5.0%}  {out['advisor']['name']} {r['bot']:>5.0%}"
                      f"  ({r['gap_cad']:+,.0f} $)")
    for n in out["notes"]:
        print(f"\n• {n}")
    print("\nOutil d'aide à la décision, pas un conseil financier. C'est toi qui passes les ordres.\n")


def cmd_web(args, cfg):
    from .web.server import serve
    serve(args.db, cfg, host=args.host, port=args.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="chubby", description="Arène de bots en argent fictif.")
    p.add_argument("--db", default="data/arena.db", help="fichier SQLite de l'arène")
    p.add_argument("--cache", default="data/cache", help="dossier de cache des prix")
    p.add_argument("--compte", default="CELI", choices=["CELI", "REER", "NON_ENREGISTRE"])
    p.add_argument("--sans-compte-usd", action="store_true",
                   help="compte en CAD seulement : 1,5 %% de conversion sur chaque trade américain")
    p.add_argument("--sans-ia", action="store_true", help="pas de versions IA ni de coach Claude")
    p.add_argument("--taux-cash", type=float, default=3.0, help="intérêt annuel sur le cash, en %% (défaut : 3)")
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
    sub.add_parser("coach-ia", help="lancer maintenant la revue des Chubbies IA par Claude").set_defaults(fn=cmd_coach_ia)
    a = sub.add_parser("conseil", help="comparer ton portefeuille au Chubby qui te ressemble")
    a.add_argument("fichier", nargs="?", help="export CSV de tes positions Wealthsimple")
    a.add_argument("--risque", type=int, default=3, choices=range(1, 6), help="1 très prudent … 5 très audacieux")
    a.add_argument("--style", default="equilibre", choices=["revenu", "croissance", "equilibre"])
    a.add_argument("--horizon", default="long", choices=["court", "moyen", "long"])
    a.add_argument("--compte-usd", action="store_true", help="tu as le forfait avec compte en USD")
    a.set_defaults(fn=cmd_conseil)
    w = sub.add_parser("web", help="ouvrir le tableau de bord dans le navigateur")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--host", default="0.0.0.0")
    w.set_defaults(fn=cmd_web)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose or args.cmd == "serve" else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    args.fn(args, ArenaConfig(account_type=args.compte, usd_account=not args.sans_compte_usd,
                              cash_rate=args.taux_cash / 100))


if __name__ == "__main__":
    main()
