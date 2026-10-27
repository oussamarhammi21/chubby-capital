# Chubby Capital

Une arène où des bots de trading, les **Chubbies**, s'affrontent avec **10 000 $ fictifs chacun** sur les vrais prix du TSX et de Wall Street. Chacun a sa personnalité d'investisseur. On suit leur progression sur un tableau de bord et une île 3D, on les coache, et le meilleur peut servir de conseiller pour un vrai portefeuille.

> Argent fictif uniquement. Ce projet n'envoie aucun ordre réel et ne constitue pas un conseil financier.

## Les Chubbies

| Chubby | Personnalité | Ce qu'il fait |
|---|---|---|
| Tortue | Indicielle passive | XIC.TO + VFV.TO, puis ne bouge plus (réinvestit seulement les dividendes) |
| Flan | Rentier · dividendes | Chaque mois, garde les 8 meilleurs rendements en dividendes (entre 2 % et 9 %, pas en chute libre) |
| Dumpling | Équilibré · diversification | Mix d'ETF (actions Canada, É.-U., international, émergents, obligations, or), rééquilibré quand un poids dérive |
| Mochi | Chasseur de tendance | Chaque semaine, vise les 5 titres les plus forts sur 3 mois parmi 100 grandes sociétés canadiennes et américaines ; garde un titre tant qu'il reste dans le top 10 et coupe ce qui casse sa moyenne 50 jours |
| Muffin | Chasseur d'aubaines | Parmi les mêmes 100 sociétés, achète les plus survendues (RSI < 30, au-dessus de la moyenne 200 jours), revend au rebond. Vend le soir à -8 % (vérifié à la clôture), avec un stop de secours à -15 % pendant la séance |
| Jalapeño | Casse-cou · options | Calls sur l'indice ou le FNB le plus en forme quand le Nasdaq est au-dessus de sa moyenne 200 jours, petits puts sinon ; 30 % du capital en primes, sortie sur gain (+100 %), stop (-50 %), retournement ou à 7 jours de l'échéance |
| Jalapeño v2 | Vendeur d'options · la roue | Vend des puts garantis par son cash sur des FNB bon marché (XLF, EEM, SLV, KRE, GDX) et encaisse la prime ; s'il est exercé, il vend des calls couverts sur ses actions. Rachète à 50 % de la prime gagnée, ou pour couper une perte. Gagne souvent un peu, peut perdre gros dans un krach |
| Jalapeño v3 | Casse-cou patient · options longues | Calls d'environ 10 mois, déjà dans la monnaie, sur le FNB de marché le plus en forme dont un contrat rentre dans son budget (QQQ et SPY sont souvent trop chers pour 10 000 $), quand la tendance est haussière : peu de perte de valeur chaque jour, un effet de levier modéré. Les renouvelle avant qu'ils ne vieillissent, range tout au cash si le Nasdaq casse sa moyenne 200 jours |
| Matcha | Apprentie machine learning | Chaque mois, réentraîne un modèle sur deux ans d'historique pour apprendre quels signaux ont annoncé les meilleurs titres, puis achète ses 8 favoris. Elle écrit dans son journal ce qu'elle a appris |

Le code est dans [`chubby/bots/`](chubby/bots/). Pour créer un Chubby, il suffit d'hériter de `Bot` et d'écrire `decide()`, qui renvoie les poids voulus. Les **réglages** (`params`) déclarés par chaque bot, avec leurs bornes, sont ce que le coaching peut ajuster.

### Les options de Jalapeño

Jalapeño **achète** des options, Jalapeño v2 en **vend** : les deux tradent côte à côte pour comparer.
Un put vendu doit être entièrement garanti par du cash libre, et un call vendu doit être couvert par 100 actions détenues.
À l'échéance, une option vendue expire sans valeur (la prime est gardée) ou est exercée (achat ou livraison des actions au prix d'exercice).

**En direct**, les options suivent les **vraies chaînes d'options de Yahoo Finance** ([`chubby/chains.py`](chubby/chains.py)) :
les Chubbies choisissent parmi les échéances et prix d'exercice qui existent vraiment, et chaque option est évaluée au
milieu de l'offre et de la demande (ou au dernier prix échangé). Ces vrais prix sont gardés dans la base, et la fiche
du Chubby les marque « prix réel ». Un contrat introuvable dans la chaîne retombe sur le modèle. `--options-modele` désactive les chaînes.

Pour les backtests, il n'existe pas d'historique gratuit des prix d'options. Elles y sont donc **évaluées avec Black-Scholes** à partir du prix du sous-jacent, avec une volatilité implicite estimée sur la volatilité des 21 dernières séances ([`chubby/options.py`](chubby/options.py)). Contrats de 100 actions, échéances mensuelles (3e vendredi), 2 % d'écart achat-vente et 0,75 $ par contrat (à ajuster selon ton courtier). À l'échéance, l'option est réglée à sa valeur intrinsèque.

### Les versions IA et le coach Claude

En direct, chaque Chubby a une **version IA** (avec une petite antenne) : même personnalité, mêmes règles, mais **chaque vendredi, Claude relit sa semaine** (performance, trades, journal, tes conseils, état du marché, résultats de son jumeau sans coach) et **ajuste ses réglages** dans leurs bornes. Il écrit sa leçon dans le journal. L'original sert de groupe témoin : le classement montre si le coaching par IA fait vraiment mieux.

- Il faut une clé API Anthropic : copie `.env.example` en `.env` et mets ta clé dedans (ou définis `ANTHROPIC_API_KEY`).
- Coût : environ 7 requêtes par semaine, soit quelques dollars par mois au plus.
- Sans clé, l'arène tourne normalement et les versions IA gardent leurs réglages.
- Les versions IA ne jouent **pas** dans les backtests : Claude connaît déjà l'histoire des marchés, ce serait tricher.

```bash
chubby coach-ia        # lancer la revue tout de suite au lieu d'attendre vendredi
chubby --sans-ia tick  # arène sans versions IA
```

## L'arène en direct

Les Chubbies **tradent pendant la bourse** (9 h 30 à 16 h, heure de Toronto), chacun selon sa personnalité
([`chubby/intraday.py`](chubby/intraday.py)) :

- **à l'ouverture**, les ordres décidés la veille au soir partent au premier cours de la séance, et sont inscrits tout de suite ;
- **toutes les 15 minutes**, chacun refait son analyse avec le cours du moment, avec ses propres règles : Muffin achète au
  creux dès que le RSI passe sous 30 et revend au rebond, Mochi coupe une tendance qui casse et place le cash qui dort sur
  les plus fortes, Jalapeño encaisse son gain ou coupe sa perte, Tortue, Flan, Dumpling et Matcha investissent le cash qui dort ;
- il **confirme** avant d'agir : l'occasion doit être encore là à la vérification suivante (pas de réaction au bruit) ;
- **pas d'aller-retour** sur un même titre dans la journée, pour ne pas payer des frais pour rien ;
- ses **ordres stop** sont surveillés en direct et exécutés dès que le cours franchit le seuil ;
- chaque ordre est noté avec **l'heure et la raison** (journal, fiche, direct et île 3D).

Le soir (16 h 20), la séance de clôture verse les dividendes et les intérêts, inscrit la valeur de clôture, fait le
classement et prend la dernière décision de la journée (exécutée à l'ouverture suivante). Si elle a été manquée,
elle est rattrapée au début de la séance suivante, avant de trader. `--sans-seance` revient à l'ancien fonctionnement
(décisions le soir seulement), et `chubby seance` lance une analyse tout de suite.

On suit aussi leur journée en direct ([`chubby/live.py`](chubby/live.py)) :

- leur capital est recalculé sur les vrais cours, par barres de 5 minutes, en tenant compte des ordres passés dans la journée ;
- on voit ce qu'ils viennent d'acheter ou de vendre, et l'occasion qu'ils s'apprêtent à confirmer ;
- on voit le titre qui les fait le plus bouger ;
- chacun **réagit selon sa personnalité** : Tortue ne bouge pas, Muffin flaire une aubaine, Jalapeño panique, Matcha prend des notes pour son prochain entraînement.

Sur le tableau de bord, la section **En direct** montre la courbe de la journée, les indices et ce que dit chaque Chubby.
Pendant la bourse, l'île 3D s'ouvre directement sur la vraie séance (bouton « ● En direct ») :

- les Chubbies sautent de joie quand leur journée s'améliore, et vont vérifier le tableau des cotes quand elle se gâte ;
- ils **parlent** : leur bulle s'affiche et leur bouche bouge. Avec le bouton « 🔊 Son », chacun a une voix de mascotte douce et un peu soufflée : un seul petit son par réplique, « hmm~ », « ooh » et un petit rire quand ça va bien, un soupir quand ça va mal ;
- quelques bruitages accompagnent la partie : pièces des dividendes, sauts, fanfare du podium.
- avec le bouton « 🎵 Musique », un petit morceau lo-fi composé tourne en fond : 16 mesures de piano Rhodes, nappe chaude, contrebasse, batterie feutrée et mélodie de kalimba. Elle est composée à la volée, donc jamais tout à fait la même, ralentit le soir et baisse quand un Chubby parle.

Les sons sont créés dans le navigateur, sans aucun fichier audio.

### Voir comment ils pensent, en direct

Chaque soir, chaque Chubby traduit ses règles en **seuils de prix** pour la séance suivante. Par exemple :

- Muffin : « HD devient une aubaine sous 61,20 $ », le prix où son RSI passe sous 30 ;
- Mochi : « je vends LIN s'il clôture sous 412 $ », 3 % sous sa moyenne 50 jours ;
- Jalapeño : « le Nasdaq passe en mode baissier sous 478 $ » ;
- Jalapeño v3 : « je vends tout si le Nasdaq clôture sous… ».

Pendant la bourse, ces seuils sont comparés aux vrais cours :

- **sur l'île**, un bandeau fait défiler le marché, et le tableau des cotes alterne entre les Chubbies et les plus gros mouvements du marché ;
- **le panneau « Ce qu'ils surveillent »**, en bas à gauche, montre pour chaque Chubby ce qui le rapproche le plus d'une décision : son seuil le plus proche avec une jauge, ou à défaut ses ordres de demain et les titres de sa watchlist qui bougent ;
- **dans la carte d'un Chubby**, des jauges montrent à quelle distance il est d'agir (« à 1,2 % », « franchi ! ») ;
- **les Chubbies parlent à tour de rôle** de ce qu'ils surveillent (« ORCL est à 2,9 % de mon seuil de rebond… »), et réagissent quand un seuil est franchi ;
- **dans le tableau de bord et la fiche**, les seuils proches ou franchis s'affichent sous chaque Chubby et dans sa watchlist.

Ils agissent toujours à la clôture : un seuil franchi à 11 h ne compte que s'il tient jusqu'à 16 h. C'est ce qui permet de repérer un comportement à corriger, par exemple un seuil trop serré qui se fait franchir tous les jours.

Le direct se rafraîchit tout seul, toutes les 4 minutes au plus, dès qu'une page est ouverte : `chubby web` suffit. Il fait aussi trader les Chubbies pendant la bourse et joue la séance du soir à 16 h 20, tant qu'il reste ouvert.

```bash
chubby direct      # la même chose dans le terminal
```

## Les règles de l'arène

Le but est d'être réaliste, pour que les résultats veuillent dire quelque chose :

- **Pas de triche sur le futur** : un bot décide le soir avec les clôtures du jour, et ses ordres sont exécutés **à l'ouverture du lendemain**. Matcha n'apprend que sur des exemples dont le résultat était déjà connu.
- **Ordres stop pendant la séance** : Muffin (stop de secours à -15 %), Jalapeño et Jalapeño v3 (-50 % sur leurs options) et Jalapeño v2 (rachat si l'option vendue vaut 3 fois la prime) laissent un ordre stop, comme chez un vrai courtier.
  - **Pourquoi -15 % pour Muffin, et pas -8 %.** Sur 2022-2025, un stop à -8 % pendant la séance lui coûtait 12 points (+23 % au lieu de +35 %). Muffin achète des titres survendus, qui plongent souvent dans la journée avant de remonter : il vendait au creux, juste avant le rebond. Son -8 % se vérifie donc à la clôture.
  - **Quand il s'exécute.** Il se déclenche dès que le plus bas, ou le plus haut, de la journée franchit le seuil. Il est exécuté au prix du stop, ou à l'ouverture si le titre a ouvert au-delà (un trou à l'ouverture).
  - **Dans les backtests.** On utilise le plus haut et le plus bas de chaque jour.
  - **En direct.** Le tableau de bord montre les stops proches (« Stop · UPS 61,05 (à 5,1 %) »), et le Chubby crie quand le sien est touché. La vente est comptabilisée à la séance du soir, au prix du stop.
- **Frais réels** : 0 $ de commission et 0,05 % de glissement à chaque exécution (2 % sur les options).
- **Un compte adapté à chaque personnalité** :
  - Mochi, Muffin, Matcha et les deux Jalapeño ont le **compte en USD** (forfait Wealthsimple de **10 $ par mois**) : pas de conversion sur leurs trades américains.
  - Tortue, Dumpling et Flan, surtout investis au Canada, ont un **compte en CAD sans forfait** : chaque trade ou dividende américain paie **1,5 % de conversion**.
  - Avec `--sans-compte-usd`, tout le monde passe au compte en CAD.
- **Compte CELI par défaut** : retenue de 15 % sur les dividendes américains (pas en REER). Option `--compte`.
- **Le cash rapporte 3 % par an** (option `--taux-cash`), versé chaque séance, comme un compte d'épargne ou des bons du Trésor. Sans ça, les Chubbies qui attendent au cash seraient injustement pénalisés.
- **Actions entières**, comptes en CAD, taux USD/CAD réel du jour.

## Le score

Le classement se fait sur un score de 0 à 100 ([`chubby/scoring.py`](chubby/scoring.py)) :
40 % rendement **par an**, 30 % Sharpe (rendement ajusté au risque), 20 % pire baisse, 10 % régularité (part des semaines qui battent le cash).

Le rendement, le Sharpe et la régularité se mesurent **par rapport au cash**, qui rapporte 3 % par an. Sans ça, un Chubby qui dort au cash aurait une courbe parfaitement lisse et finirait premier. Rester au cash vaut donc 55 sur 100 : pour faire mieux, il faut battre le cash.
Un bot ne peut donc pas gagner juste en prenant des risques énormes.

Chaque séance donne de l'XP : 10 pour tout le monde, plus 30, 20 et 10 pour le podium. Ton coaching donne 15 XP, celui de Claude 25.

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

# Tourner en continu : une séance chaque jour de semaine à 16 h 20, revue IA le vendredi,
# et le tableau de bord (avec le direct) dans le même programme
chubby serve --web

chubby direct                   # où en sont les Chubbies en ce moment
chubby seance                   # une analyse en séance tout de suite (chacun trade s'il voit une occasion)
chubby watchlist                # recalculer watchlists et seuils de la dernière séance (sans rien rejouer)

chubby classement
chubby ordres                   # les ordres préparés pour la prochaine ouverture (ou : chubby ordres mochi)
chubby journal matcha
chubby coach jalapeno moins     # moins | plus | garde : change son agressivité
chubby conseil positions.csv --risque 2 --style revenu
```

Tout est enregistré dans un fichier SQLite (`data/arena.db`) : comptes, réglages, trades, valeurs quotidiennes, journal de chaque bot et **événements** (`trade`, `stop`, `analyse`, `dividend`, `rank_change`, `coach`, `ai_coach`…), que les îles 3D animent.

## Au quotidien

Pour que les Chubbies **tradent pendant la bourse**, le tableau de bord doit tourner pendant la séance
(`chubby web`, ou `chubby serve --web`) : c'est lui qui les fait analyser et trader toutes les 15 minutes, et qui joue la
séance du soir à 16 h 20 tant qu'il reste ouvert. Ouvre-le le matin, laisse la fenêtre ouverte, c'est tout.

Sans lui, il reste **la séance du soir** (`chubby tick`, après 16 h 20, heure de Toronto) : les Chubbies y comptent leur
argent et décident pour le lendemain, et leurs ordres partiront à l'ouverture suivante. Le vendredi, Claude coache aussi les versions IA.

Si ton PC était éteint un soir, le `tick` suivant rattrape les séances manquées, chacune avec ses vrais cours de clôture.

**Option 1 : automatique** (Windows). Le Planificateur de tâches lance la séance chaque soir de semaine à 16 h 30.
Si le PC est éteint ou en veille à cette heure-là, ce n'est pas grave : la séance suivante rattrape.
Le script utilise l'environnement `.venv` du dossier du projet, celui créé à l'installation.
Dans PowerShell, remplace le chemin par celui de ton dossier :

```powershell
schtasks /Create /TN "Chubby Capital" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:30 /TR "C:\chemin\vers\chubby-capital\scripts\tick.bat"
```

Le compte rendu de chaque séance s'écrit dans `data\tick.log`.
Pour regarder, double-clique sur `scripts\tableau.bat` : il ouvre le tableau de bord dans le navigateur, avec le direct si la bourse est ouverte.
Ferme la fenêtre quand tu as fini.

**Option 2 : tout en continu** (le PC serveur) : `chubby serve --web` joue la séance à 16 h 20 et garde le tableau de bord ouvert.
Le PC ne doit pas se mettre en veille.

La clé API (fichier `.env` à la racine du projet) est lue par toutes les commandes, avec ou sans Docker.

## Tableau de bord

```bash
chubby web                                  # arène en direct : http://localhost:8000
chubby --db data/backtest.db web            # ou n'importe quel backtest
```

Direct pendant la bourse, podium, classement, courbes de capital, duels **original contre version IA**
(les deux courbes depuis le premier jour de la version IA, avec l'écart), derniers événements, et la fiche de chaque Chubby
(réglages, positions, journal, trades) en cliquant dessus. La page se met à jour toute seule chaque minute.
Elle n'a besoin d'aucune connexion Internet à part les polices.

### La fiche complète de chaque Chubby

Bouton « Fiche complète » dans sa fiche du tableau de bord ou sur l'île, ou http://localhost:8000/chubby?id=muffin.
Elle se présente comme un compte de courtage, avec en haut :

- **sa valeur**, avec la variation du jour (en direct pendant la bourse) et sa courbe sur 1 semaine, 1 mois, 3 mois, cette année, 1 an ou toute la saison ;
- **ses positions**, avec la répartition, le coût moyen, la variation du jour et le rendement total ;
- **son activité récente** : chaque ordre se déplie pour montrer quand il a été décidé et exécuté, le prix, le taux de change et les frais ;
- **son cash disponible** et **ses ordres pour la prochaine ouverture** ;
- **sa watchlist** : les titres qu'il surveille sans les détenir, et pourquoi. Par exemple pour Mochi « 6e du classement : +27 % sur 63 séances », pour Muffin « RSI 34 : il attend qu'il passe sous 30 ». Elle est calculée chaque soir avec ses propres règles, et suit les vrais cours pendant la bourse.

Plus bas, tout ce qu'il faut pour le comprendre :

- **comment il pense** : ses règles en clair, avec la valeur actuelle de ses réglages ;
- **ses réglages** : actuels, de départ et leurs bornes (ce que le coaching a changé apparaît en vert) ;
- **ses chiffres** : rendement, pire baisse, Sharpe, part d'allers-retours gagnants, gains réalisés, dividendes, frais, stops, coachings ;
- **sa courbe** de capital, à côté de celle de son jumeau (original ou IA), et ses baisses depuis chaque sommet ;
- **ses positions**, avec le prix moyen payé, le gain et l'ordre stop en place ;
- **chaque aller-retour** sur un titre (de l'achat à la revente complète) avec son gain, et son **bilan mois par mois** ;
- **tout ce qu'il a fait, jour par jour**, dans l'ordre d'une séance :
  - à l'ouverture, les ordres décidés la veille au soir ;
  - pendant la séance, les ordres stop ;
  - à la clôture, les dividendes, les frais et le classement ;
  - le soir, ce qu'il a décidé et écrit dans son journal, et le coaching reçu.

L'historique se filtre par type, et s'exporte en CSV pour Excel ou Google Sheets.

### La ville des Chubbies (3D)

Bouton « Voir l'île », ou http://localhost:8000/ile. Ambiance centre-ville la nuit : chaque Chubby a **son propre
pâté de maisons**, entre des rues mouillées et des gratte-ciel aux fenêtres allumées. On y trouve sa maison et son
coffre de pièces, son **écran d'analyse** (ses seuils de décision et sa watchlist, le titre qu'il regarde surligné),
le **marché** où il achète et vend, et son **panier** où il range une caisse par position.

Chaque geste vient d'un vrai événement de la journée (`/api/activity` et `/api/live`) :

- **achat** : il analyse le titre à son écran, paie au marché, rapporte la caisse dans son panier ;
- **vente** ou **stop touché** : il reprend la caisse, l'apporte au marché, les pièces reviennent dans son coffre ;
- **analyse** : il regarde le titre à la loupe en attendant la confirmation ;
- **entre deux** : il refait ce qu'il a vraiment en tête (seuil proche, stop qui approche, titre de sa watchlist qui bouge) ;
  bourse fermée, il dort chez lui.

La page montre la séance en direct (nouveaux ordres toutes les 15 secondes, cours toutes les minutes) : rien n'est
rejoué tout seul quand tu reviens.

- **Mosaïque** : tous les quartiers côte à côte, chacun avec sa caméra, ce qu'il fait et ce qu'il dit. À droite,
  le **classement du jour**, le **classement de la saison** et **ce qui s'est passé** (tous les achats, ventes, stops
  et analyses de la journée, filtrables et exportables en CSV). Un clic sur un événement ouvre le Chubby et le rejoue.
- Clique sur un quartier pour le **suivre** : caméra libre (glisser, molette, double-clic), ses seuils, son panier,
  ce qu'il a en tête, et **sa journée** avec, pour chaque action, l'heure, la quantité, le prix, le montant et la
  raison. Le bouton **⏪ Rejouer sa journée** la rejoue devant toi, « ▶ Revoir » rejoue une seule action, le menu
  **Jour** remonte aux séances précédentes, et **⬇ Journal CSV** télécharge le tout. Flèches ← → pour passer au
  suivant, Échap pour revenir. Lien direct : `/ile#muffin`.
- **La ville** : tous les quartiers d'un coup d'œil, avec le même classement et le même fil d'événements.
- **Versions IA** ajoute les quartiers des jumeaux coachés par Claude.
- **⏩ x1 / x2 / x3** accélère les Chubbies (marche, pauses, rejeux, bulles) ; la vitesse choisie est retenue.

L'ancienne grande île (avec le podium et le replay de la saison) reste sur http://localhost:8000/ile-3d, et la
carte illustrée sur http://localhost:8000/carte.

### Le mode conseiller

http://localhost:8000/conseiller (ou `chubby conseil`). Tu décris l'investisseur que tu veux être (goût du
risque, revenus ou croissance, horizon, compte en USD ou non) et tu ajoutes l'**export CSV de tes positions
Wealthsimple**. L'arène te propose les Chubbies qui te ressemblent, classés par affinité et par score, puis
compare ton portefeuille à celui du meilleur :

- les **grandes lignes** côte à côte : nombre de titres, plus grosse position, poids des 5 plus grosses, part américaine, FNB indiciels larges ;
- puis **titre par titre**. Un titre que le Chubby ne détient pas est marqué « hors de son style », ce qui n'est pas un signal de vente ;
- des remarques sur la concentration, la part américaine et le forfait USD.

- **Lecture seule** : le fichier n'est jamais enregistré, seul ton profil l'est. C'est toi qui passes les ordres.
- Un Chubby est **« certifié »** après 120 séances d'arène (environ six mois) ; avant, ses idées sont des pistes.
- Le lecteur accepte :
  - **l'export des activités Wealthsimple** (tout l'historique de tes comptes) : il reconstruit tes positions à partir des achats,
    ventes et opérations sur titres, puis cherche leur prix du jour sur Yahoo Finance (gardé en cache pour la journée).
    Le compte crypto et les options sont ignorés ; un titre sans cours est évalué à ton dernier prix payé, et c'est signalé ;
  - ou tout CSV de positions avec des colonnes `Symbol`, `Quantity` et `Market Value` (ou `Market Price`).
    Les titres d'une bourse canadienne reçoivent automatiquement le suffixe `.TO`.

## Sur le PC serveur (Docker)

Le guide pas à pas (Ubuntu Server ou Windows, Docker, Tailscale, sauvegardes) est dans [`docs/SERVEUR.md`](docs/SERVEUR.md).

```bash
cp .env.example .env          # puis mets ta clé API Anthropic dedans (facultatif)
docker compose up -d          # l'arène (« chubby serve ») + le tableau de bord sur le port 8000
docker compose logs -f
docker compose run --rm arene chubby classement
```

Les données restent dans `./data` sur le PC.

## Feuille de route

- [x] Moteur : données yfinance, courtier fictif, score, journal, événements
- [x] Tableau de bord web et API JSON (`/api/overview`, `/api/equity`, `/api/bots/<id>`, `/api/replay`)
- [x] Île 3D branchée sur l'arène (rejeu des séances, coaching)
- [x] Coach Claude hebdomadaire et versions IA contre originaux
- [x] Matcha, bot de machine learning réentraîné chaque mois
- [x] Jalapeño aux options (évaluées avec Black-Scholes)
- [x] Profil investisseur et mode conseiller (export CSV Wealthsimple, lecture seule)
- [x] Prix d'options réels en direct (chaînes d'options de Yahoo) au lieu du modèle
- [x] Courbes « original contre version IA » côte à côte dans le tableau de bord
- [x] Arène en direct pendant la bourse : capital toutes les 5 minutes et réactions des Chubbies
- [x] Voix, bruitages et musique d'ascenseur sur l'île 3D
- [x] Ordres stop déclenchés pendant la séance
