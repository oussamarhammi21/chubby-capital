# Chubby Capital

Une arène où six bots de trading, les **Chubbies**, s'affrontent avec **10 000 $ fictifs chacun** sur les vrais prix du TSX et de Wall Street. Chacun a sa personnalité d'investisseur. On suit leur progression, on les coache, et un jour le meilleur pourra conseiller un vrai portefeuille.

Ce dépôt contient le **moteur** (Python). L'île 3D viendra se brancher dessus plus tard.

> Argent fictif uniquement. Ce projet n'envoie aucun ordre réel et ne constitue pas un conseil financier.

## Les Chubbies

| Chubby | Personnalité | Ce qu'il fait |
|---|---|---|
| Tortue | Indicielle passive | 50 % XIC.TO + 50 % VFV.TO, puis ne bouge plus (réinvestit seulement les dividendes) |
| Flan | Rentier · dividendes | Chaque mois, garde les 8 meilleurs rendements en dividendes (entre 2 % et 9 %, pas en chute libre) |
| Dumpling | Équilibré · diversification | Mix fixe d'ETF (actions Canada, É.-U., international, émergents, obligations, or), rééquilibré si un poids dérive de plus de 5 points |
| Mochi | Chasseur de tendance | Chaque semaine, vise les 5 titres les plus forts sur 3 mois parmi 100 grandes sociétés canadiennes et américaines ; garde un titre tant qu'il reste dans le top 10 et coupe ce qui casse sa moyenne 50 jours |
| Muffin | Chasseur d'aubaines | Parmi les mêmes 100 sociétés, achète les plus survendues (RSI < 30, au-dessus de la moyenne 200 jours), revend au rebond, stop à -8 % |
| Jalapeño | Casse-cou · levier | Tout sur le FNB à levier le plus en forme (TQQQ, SOXL, UPRO, TNA), gardé au moins 7 séances sauf s'il s'effondre ; tout au cash quand le Nasdaq passe sous sa moyenne 200 jours. Les options viendront en v2 |

Le code de chaque Chubby est dans [`chubby/bots/chubbies.py`](chubby/bots/chubbies.py). Pour en créer un nouveau, il suffit d'hériter de `Bot` et d'écrire `decide()`, qui renvoie les poids voulus.

## Les règles de l'arène

Le but est d'être réaliste, pour que les résultats veuillent dire quelque chose :

- **Pas de triche sur le futur** : un bot décide le soir avec les clôtures du jour, et ses ordres sont exécutés **à l'ouverture du lendemain**.
- **Frais réels** : 0 $ de commission et 0,05 % de glissement à chaque exécution.
- **Compte en USD par défaut**, comme un forfait Wealthsimple à **10 $ par mois** (prélevés chaque mois sur chaque Chubby) : pas de conversion sur les trades américains. Avec `--sans-compte-usd`, chaque trade ou dividende américain paie **1,5 % de conversion** CAD ↔ USD, et il n'y a pas de forfait.
- **Compte CELI par défaut** : retenue de 15 % sur les dividendes américains (pas en REER). Option `--compte`.
- **Actions entières**, comptes en CAD, taux USD/CAD réel du jour.

## Le score

Le classement se fait sur un score de 0 à 100 ([`chubby/scoring.py`](chubby/scoring.py)) :
40 % rendement, 30 % Sharpe (rendement ajusté au risque), 20 % pire baisse, 10 % régularité (part des semaines positives).
Un bot ne peut donc pas gagner juste en prenant des risques énormes.

Chaque séance donne de l'XP : 10 pour tout le monde, plus 30, 20 et 10 pour le podium. Coacher un Chubby donne 15 XP.

## Installation

Python 3.11 ou plus récent.

Linux / macOS :

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Windows (PowerShell) :

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

Si PowerShell refuse d'activer l'environnement, lance une fois
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Si la commande `chubby` reste introuvable,
`python -m chubby.cli` fait exactement la même chose (ex. `python -m chubby.cli classement`).

## Utilisation

```bash
# Rejouer une période passée (base séparée pour ne pas mélanger avec l'arène en direct)
chubby --db data/backtest.db backtest --start 2024-01-01 --end 2025-12-31

# Jouer la séance du jour (à lancer après 16 h, heure de Toronto)
chubby tick

# Tourner en continu : une séance chaque jour de semaine à 16 h 20
chubby serve

chubby classement
chubby journal flan
chubby coach jalapeno moins     # moins | plus | garde : change son agressivité
```

Tout est enregistré dans un fichier SQLite (`data/arena.db`) : comptes, trades, valeurs quotidiennes, journal de chaque bot et **événements** (`trade`, `dividend`, `rank_change`, `leader`). Ce sont ces événements que l'île 3D animera.

## Tableau de bord

```bash
chubby web                                  # arène en direct : http://localhost:8000
chubby --db data/backtest-v2.db web         # ou n'importe quel backtest
```

Podium, classement, courbes de capital, derniers événements, et la fiche de chaque Chubby
(positions, journal, trades) en cliquant dessus. La page se met à jour toute seule chaque minute.
Elle n'a besoin d'aucune connexion Internet à part les polices.

## Sur le PC serveur (Docker)

```bash
docker compose up -d          # l'arène (« chubby serve ») + le tableau de bord sur le port 8000
docker compose logs -f
docker compose run --rm arene chubby classement
```

Les données restent dans `./data` sur le PC.

## Feuille de route

- [x] Moteur : données yfinance, courtier fictif, 6 Chubbies, score, journal, événements
- [x] Tableau de bord web et API JSON (`/api/overview`, `/api/equity`, `/api/bots/<id>`)
- [ ] Coach Claude hebdomadaire qui ajuste les réglages de chaque Chubby
- [ ] Bot ML réentraîné chaque mois
- [ ] Île 3D (React Three Fiber) branchée sur le moteur
- [ ] Jalapeño aux vraies options
- [ ] Profil investisseur et mode conseiller pour un compte Wealthsimple (lecture seule, via export CSV)
