# Installer l'arène sur le PC serveur

L'arène tourne en continu dans Docker : une séance chaque jour de bourse à 16 h 20 (heure de Toronto),
la revue du coach Claude le vendredi, et le tableau de bord sur le port 8000. Avec Tailscale, tu
l'ouvres depuis ton téléphone ou ton laptop, de n'importe où, sans rien exposer sur Internet.

Compte environ une heure la première fois.

## Option A (recommandée) : Ubuntu Server

### 1. Installer Ubuntu Server
1. Sur ton PC principal, télécharge **Ubuntu Server 24.04 LTS** sur ubuntu.com et copie-le sur une clé USB
   avec **balenaEtcher** ou **Rufus**. ⚠️ L'installation efface le disque du PC serveur.
2. Démarre le PC serveur sur la clé (touche F12, F11 ou Échap au démarrage selon la marque).
3. Pendant l'installation : choisis un nom de machine (par exemple `arene`) et **coche « Install OpenSSH server »**.
4. Dans le BIOS, active si possible **« Restore on AC power loss » = On** : le PC redémarre tout seul après une panne de courant.

Ensuite, tu peux tout faire depuis ton PC principal : `ssh ton-nom@adresse-du-serveur`.

### 2. Docker
```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```
Déconnecte-toi et reconnecte-toi pour que le dernier changement prenne effet.

### 3. Tailscale (accès à distance privé)
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Ouvre le lien affiché pour connecter le serveur à ton compte Tailscale, puis installe l'app Tailscale sur
ton téléphone et ton laptop **avec le même compte**.

### 4. Récupérer le projet
Le dépôt est privé : le plus simple est l'outil GitHub.
```bash
sudo apt install -y gh
gh auth login            # suis les questions : GitHub.com, HTTPS, connexion par le navigateur
gh repo clone oussamarhammi21/chubby-capital
cd chubby-capital
```

### 5. La clé du coach Claude (facultatif)
```bash
cp .env.example .env
nano .env                # colle ta clé après ANTHROPIC_API_KEY=, puis Ctrl+O, Entrée, Ctrl+X
```
Sans clé, tout fonctionne, sauf le coaching des versions IA.

### 6. Lancer
```bash
docker compose up -d --build
docker compose logs -f arene      # tu dois voir « Prochaine séance : … » (Ctrl+C pour quitter les logs)
```
Le tableau de bord est sur **http://arene:8000** (le nom de ta machine dans Tailscale) depuis tous tes appareils
connectés à Tailscale, et sur http://localhost:8000 sur le serveur lui-même.

La première séance a lieu au prochain jour de bourse à 16 h 20. L'arène commence ce jour-là, sans rejouer le passé :
le tableau de bord et l'île se remplissent au fil des semaines.

## Option B : garder Windows sur le PC serveur

1. Installe **Docker Desktop** (il active WSL 2 au besoin) et coche « Start Docker Desktop when you sign in ».
2. Installe **Tailscale** pour Windows et connecte-le à ton compte.
3. Installe **Git** et **GitHub CLI** (`winget install Git.Git GitHub.cli`), puis dans PowerShell :
   ```powershell
   gh auth login
   gh repo clone oussamarhammi21/chubby-capital
   cd chubby-capital
   copy .env.example .env    # puis mets ta clé dans .env avec le Bloc-notes
   docker compose up -d --build
   ```
4. Dans les paramètres d'alimentation de Windows, désactive la **mise en veille** : l'arène doit tourner à 16 h 20.

## Au quotidien

| Pour… | Commande (dans le dossier chubby-capital) |
|---|---|
| Voir le classement | `docker compose exec arene chubby classement` |
| Lire le journal d'un Chubby | `docker compose exec arene chubby journal mochi-ia` |
| Lancer le coach Claude tout de suite | `docker compose exec arene chubby coach-ia` |
| Installer une nouvelle version | `git pull && docker compose up -d --build` |
| Voir ce qui se passe | `docker compose logs -f arene` |
| Arrêter / relancer | `docker compose down` / `docker compose up -d` |

## Sauvegarde

Toute l'arène tient dans le dossier `data/` (la base `arena.db` et le cache des prix). Copie-le de temps en temps
ailleurs, par exemple chaque nuit avec `crontab -e` sous Ubuntu :
```
0 3 * * * cp ~/chubby-capital/data/arena.db ~/sauvegardes/arena-$(date +\%F).db
```
(crée d'abord le dossier avec `mkdir -p ~/sauvegardes`).

## Sécurité

- Le tableau de bord n'a **pas de mot de passe** : il est fait pour rester dans ton réseau Tailscale.
  **N'ouvre jamais le port 8000 sur ta box Internet.**
- Le fichier `.env` contient ta clé API : il n'est jamais envoyé sur GitHub (il est dans `.gitignore`).
