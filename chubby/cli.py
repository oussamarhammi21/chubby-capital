"""Commandes : chubby backtest | tick | serve | direct | seance | classement | ordres | journal | coach | coach-ia | conseil | web."""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
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
    print(f"\n{'#':>2}  {'Chubby':<15} {'Capital':>12} {'Saison':>8} {'Pire baisse':>12} {'Sharpe':>7}"
          f" {'Trades':>7} {'Frais':>9} {'Score':>6}")
    for i, e in enumerate(arena.leaderboard(), 1):
        m = e["metrics"]
        n, fees = costs.get(e["id"], (0, 0.0))
        print(f"{i:>2}  {e['name']:<15} {e['equity']:>10,.0f} $ {m.total_return:>+8.1%} {m.max_drawdown:>12.1%}"
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


def last_closed_day(now: datetime):
    """Dernier jour dont la clôture est connue : avant 16 h 10 (heure de Toronto), la séance du jour n'est pas finie."""
    now = now.astimezone(TORONTO)
    return now.date() if (now.hour, now.minute) >= (16, 10) else now.date() - timedelta(days=1)


def cmd_tick(args, cfg):
    now = datetime.now(TORONTO)
    today = last_closed_day(now)
    if today != now.date() and now.weekday() < 5:
        print("La bourse n'a pas encore fermé : la séance d'aujourd'hui se jouera après 16 h 10 (heure de Toronto).")
    store = Store(args.db)
    data = _load(today - timedelta(days=10), today, cfg, args.cache, refresh=True)
    arena = Arena(store, data, all_bots(ia=not args.sans_ia), cfg, chains=_chains(args, today))
    # Premier lancement : l'arène commence aujourd'hui, sans rejouer le passé.
    start = arena.last_day + pd.Timedelta(days=1) if arena.last_day is not None else today
    n = arena.run(start, today)
    print(f"{n} nouvelle(s) séance(s) jouée(s).")
    if n and not args.sans_ia and arena.last_day.weekday() == 4:  # le vendredi, revue de la semaine
        _weekly_review(arena)
    _print_board(arena)


def _chains(args, today):
    """Vraies chaînes d'options pour la séance du jour, sauf avec --options-modele."""
    if args.options_modele:
        return None
    from .chains import YahooChains
    return YahooChains(today)


def _refresher(args):
    if args.sans_direct:
        return None
    from .live import Refresher
    return Refresher(args.db, chains_factory=None if args.options_modele else _chains_factory)


def _trader(args, cfg):
    from .intraday import SessionTrader
    return SessionTrader(args.db, cfg, all_bots(ia=not args.sans_ia),
                         load_daily=lambda today: _load(today - timedelta(days=10), today - timedelta(days=1), cfg, args.cache),
                         chains_factory=None if args.options_modele else _chains_factory)


def _start_session(args, cfg, evening: bool = False):
    """En arrière-plan : les Chubbies analysent et tradent toutes les 15 minutes pendant la bourse.

    Avec evening=True (tableau de bord lancé seul), la séance du soir est aussi jouée à 16 h 20.
    """
    if args.sans_seance:
        return None
    import threading
    trader = _trader(args, cfg)

    def loop():
        store = Store(args.db)
        played = None
        while True:
            trader(store)
            now = datetime.now(TORONTO)
            if evening and now.weekday() < 5 and (now.hour, now.minute) >= (16, 20) and played != now.date():
                played = now.date()
                if store.get_meta("last_day") != str(now.date()):
                    try:
                        cmd_tick(args, cfg)
                    except Exception:
                        logging.exception("La séance du soir a échoué, nouvel essai à la prochaine ouverture.")
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="seance").start()
    print("Séance en direct : pendant la bourse, les Chubbies refont leur analyse et tradent toutes les 15 minutes.")
    return trader


def cmd_seance(args, cfg):
    """Une analyse en séance tout de suite : chaque Chubby regarde les cours du moment et trade s'il le faut."""
    trader = _trader(args, cfg)
    now = datetime.now(TORONTO)
    from .live import market_open
    if not market_open(now):
        raise SystemExit("La bourse est fermée : les Chubbies tradent entre 9 h 30 et 16 h (heure de Toronto), en semaine.")
    print("Chargement des prix…", flush=True)
    status = trader.step(Store(args.db), now)
    if status.get("problem"):
        raise SystemExit(status["problem"])
    store = Store(args.db)
    rows = store.db.execute("SELECT e.bot_id, e.kind, e.payload, b.name FROM events e JOIN bots b ON b.id = e.bot_id"
                            " WHERE e.day = ? AND e.kind IN ('trade', 'stop', 'analyse') ORDER BY e.id",
                            (str(now.date()),)).fetchall()
    import json
    for r in rows:
        p = json.loads(r["payload"])
        if p.get("intraday") and p.get("time") in (status["checked_at"], "09:30"):
            what = f"{p.get('side', '')} {p.get('qty', '')} {p.get('ticker', '')}".strip() if r["kind"] != "analyse" else ""
            print(f"  {p['time']}  {r['name']:<15} {r['kind']:<8} {what:<22} {p.get('why', '')}")
    print(f"\nAnalyse de {status['checked_at']} terminée. Prochaine dans {status['every_min']} minutes avec chubby web.\n")


def _chains_factory(today):
    from .chains import YahooChains
    return YahooChains(today)


def cmd_direct(args, cfg):
    """Affiche la séance en direct : où en est chaque Chubby en ce moment, et ce qu'il en dit."""
    from .live import pulse
    store = Store(args.db)
    today = datetime.now(TORONTO).date()
    live = pulse(store, chains=_chains(args, today))
    if not live.get("ok"):
        raise SystemExit(live.get("error"))
    state = "bourse ouverte" if live["open"] else "bourse fermée"
    print(f"\nSéance du {live['day']} à {live['as_of']} ({state})   "
          + "   ".join(f"{i['name']} {i['change']:+.2%}" for i in live["indexes"]))
    for e in live["bots"]:
        print(f"  {e['name']:<16} {e['equity']:>10,.0f} $ {e['change']:>+7.2%} {e['change_cad']:>+8,.0f} $   « {e['reaction']} »")
    print()


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


def cmd_watch(args, cfg):
    """Recalcule les watchlists et les seuils de décision de la dernière séance (sans rien rejouer)."""
    store = Store(args.db)
    last = store.get_meta("last_day")
    if last is None:
        raise SystemExit("L'arène n'a encore joué aucune séance.")
    print("Chargement des prix…", flush=True)
    data = _load(pd.Timestamp(last) - pd.Timedelta(days=10), last, cfg, args.cache)
    n = Arena(store, data, all_bots(ia=not args.sans_ia), cfg).refresh_watchlists()
    print(f"Watchlists et seuils recalculés pour {n} Chubbies (séance du {last}).")


def cmd_serve(args, cfg):
    """Tourne en continu : une séance chaque jour de semaine à 16 h 20, heure de Toronto.

    Avec --web, le tableau de bord (et le direct pendant la bourse) tourne dans le même programme.
    """
    if args.web:
        from .web.server import serve
        serve(args.db, cfg, host=args.host, port=args.port, live=_refresher(args), block=False)
    _start_session(args, cfg)
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


def cmd_orders(args, cfg):
    """Les ordres que chaque Chubby a préparés pour la prochaine ouverture."""
    from .options import describe, is_option
    from .report import pending_orders
    store = Store(args.db)
    bots = [args.bot] if args.bot else [r["id"] for r in store.db.execute("SELECT id FROM bots ORDER BY id")]
    shown = 0
    for bot_id in bots:
        row = store.bot_row(bot_id)
        if row is None:
            raise SystemExit(f"Chubby inconnu : {bot_id}")
        p = pending_orders(store, bot_id)
        if not p or not p["orders"]:
            continue
        shown += 1
        print(f"\n{row['name']} (décidé le {p['decided_on']}, exécuté à la prochaine ouverture)")
        for o in p["orders"]:
            name = describe(o["ticker"]) if is_option(o["ticker"]) else o["ticker"]
            if "qty" in o:
                print(f"  {o['action']:<24} {o['qty']} × {name}")
            else:
                print(f"  {o['action']:<24} ~{o['amount_cad']:>8,.0f} $  {name:<10} (poids {o['weight_now']:.0%} -> {o['weight_target']:.0%})")
        if p["cash_target"] is not None:
            print(f"  {'garder en cash':<24} ~{p['cash_target']:.0%} du capital")
    if not shown:
        print("Aucun ordre en attente : les Chubbies gardent leurs positions à la prochaine ouverture.")
    print()


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
    out = advice(Store(args.db), cfg, profile, text, cache_dir=args.cache)
    print(f"\nTon portefeuille : {out['total_cad']:,.0f} $ CAD")
    print("\nLes Chubbies qui te ressemblent :")
    for m in out["matches"]:
        status = "certifié" if m["certified"] else f"en observation ({m['sessions']} séances)"
        print(f"  {m['name']:<14} affinité {m['fit']:>3}  score {m['score']:>3}  saison {m['return']:+.1%}  {status}")
    if "advisor" in out and out.get("style"):
        name = out["advisor"]["name"]
        print(f"\n{'Grandes lignes':<30} {'toi':>6} {name:>12}")
        for row in out["style"]:
            f = (lambda v: f"{v:.0f}") if row["kind"] == "n" else (lambda v: f"{v:.0%}")
            print(f"  {row['label']:<28} {f(row['mine']):>6} {f(row['bot']):>12}")
    if "advisor" in out:
        print(f"\nPour ressembler à {out['advisor']['name']} :")
        for r in out["rows"]:
            if r["action"] in ("ajouter", "réduire"):
                print(f"  {r['action']:<8} {r['symbol']:<12} toi {r['mine']:>5.0%}  {out['advisor']['name']} {r['bot']:>5.0%}"
                      f"  ({r['gap_cad']:+,.0f} $)")
    for n in out["notes"]:
        print(f"\n• {n}")
    print("\nOutil d'aide à la décision, pas un conseil financier. C'est toi qui passes les ordres.\n")


def cmd_web(args, cfg):
    from .web.server import serve
    _start_session(args, cfg, evening=True)
    serve(args.db, cfg, host=args.host, port=args.port, live=_refresher(args))


def load_env(path: str | Path = ".env"):
    """Lit le fichier .env (ex. ANTHROPIC_API_KEY=...) sans écraser une variable déjà définie."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(prog="chubby", description="Arène de bots en argent fictif.")
    p.add_argument("--db", default="data/arena.db", help="fichier SQLite de l'arène")
    p.add_argument("--cache", default="data/cache", help="dossier de cache des prix")
    p.add_argument("--compte", default="CELI", choices=["CELI", "REER", "NON_ENREGISTRE"])
    p.add_argument("--sans-compte-usd", action="store_true",
                   help="compte en CAD seulement : 1,5 %% de conversion sur chaque trade américain")
    p.add_argument("--sans-ia", action="store_true", help="pas de versions IA ni de coach Claude")
    p.add_argument("--options-modele", action="store_true",
                   help="évaluer les options avec le modèle même en direct (pas de chaînes Yahoo)")
    p.add_argument("--sans-direct", action="store_true", help="pas de suivi en direct pendant la bourse")
    p.add_argument("--sans-seance", action="store_true",
                   help="les Chubbies ne tradent pas pendant la bourse (seulement le soir, comme avant)")
    p.add_argument("--taux-cash", type=float, default=3.0, help="intérêt annuel sur le cash, en %% (défaut : 3)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="rejouer une période passée")
    b.add_argument("--start", required=True)
    b.add_argument("--end", default=str(datetime.now(TORONTO).date()))
    b.set_defaults(fn=cmd_backtest)
    sub.add_parser("tick", help="jouer la ou les séances du jour").set_defaults(fn=cmd_tick)
    sv = sub.add_parser("serve", help="tourner en continu (une séance par jour)")
    sv.add_argument("--web", action="store_true", help="lancer aussi le tableau de bord")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="0.0.0.0")
    sv.set_defaults(fn=cmd_serve)
    sub.add_parser("direct", help="où en sont les Chubbies en ce moment").set_defaults(fn=cmd_direct)
    sub.add_parser("seance", help="une analyse en séance maintenant : chaque Chubby trade s'il voit une occasion"
                   ).set_defaults(fn=cmd_seance)
    sub.add_parser("watchlist", help="recalculer les watchlists et seuils de la dernière séance").set_defaults(fn=cmd_watch)
    sub.add_parser("classement", help="afficher le classement").set_defaults(fn=cmd_board)
    j = sub.add_parser("journal", help="lire le journal d'un Chubby")
    j.add_argument("bot")
    j.add_argument("-n", type=int, default=15)
    j.set_defaults(fn=cmd_journal)
    o = sub.add_parser("ordres", help="les ordres préparés pour la prochaine ouverture")
    o.add_argument("bot", nargs="?", help="un seul Chubby (ex. mochi) ; tous par défaut")
    o.set_defaults(fn=cmd_orders)
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
