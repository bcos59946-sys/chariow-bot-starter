# Chariow Bot — starter sécurisé

Bot Telegram de commerce numérique utilisant **Chariow uniquement** pour créer les paiements.

## Important
- Le paiement Chariow peut renvoyer une URL hébergée. Ce starter ne prétend pas que le paiement peut être réalisé entièrement dans la conversation Telegram : cela dépend de l'interface de paiement proposée par Chariow.
- Aucune récompense/commande payée n'est confirmée par un clic sur « Payer ». La confirmation doit venir d'un Pulse signé et/ou d'une vérification serveur auprès de Chariow.
- Le bot charge le catalogue Chariow et demande à l'utilisateur de sélectionner un produit. Aucun produit ni prix n'est prédéfini.
- L'envoi d'argent par l'administrateur à un utilisateur (payout) n'est pas implémenté : il faut une API de versement officiellement prise en charge. Ce bot ne simulera jamais un versement.

## Structure
- `chariow_bot/` : bot Python, commandes et flux de checkout.
- `cloudflare-worker/` : Worker de réception des Pulses Chariow avec vérification HMAC-SHA256 et déduplication par livraison. Il renvoie une erreur si la signature n'est pas valide.
- `scripts/prepare_rebuild.sh` : sauvegarde l'ancien projet avant de mettre de côté l'ancien `bot.py`. Ne supprime pas les fichiers de configuration ni les clés.
- `.env.example` : noms des variables uniquement, aucun secret réel.
- `config.example.json` : exemple sans clé réelle.

## Installation Termux
```bash
pkg update
pkg install python git
cd ~/cei_bot
# Décompresse le ZIP dans un dossier séparé pour l'auditer d'abord.
unzip ~/storage/downloads/chariow-bot-starter.zip -d ~/chariow-bot-starter
cd ~/chariow-bot-starter
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
Remplis `.env` localement, ne publie jamais ce fichier.

## Variables requises
- `TELEGRAM_BOT_TOKEN`
- `CHARIOW_API_KEY` (scope minimal : `checkout.write`; ajoute `sales.read` seulement si le code de vérification par API est activé)
- `ADMIN_TELEGRAM_IDS` (IDs numériques séparés par des virgules)
- `PULSE_SHARED_SECRET` est destiné au Worker Cloudflare, pas au dépôt Git.
- `PUBLIC_BOT_URL` facultatif pour les liens de retour, pas une preuve de paiement.

## Démarrage
```bash
python -m chariow_bot.main
```

Le bot démarre en mode prudent : il crée un checkout Chariow réel seulement si les clés et paramètres requis sont présents. Il n'offre aucune simulation de paiement.

## Commandes
- `/start` : menu client
- `/products` : produit configuré
- `/buy` : démarrer une commande
- `/orders` : état local des commandes (l'état `pending` n'est pas une preuve de paiement)
- `/admin` : menu admin, réservé aux IDs autorisés
- `/tools` : affiche les outils/commandes disponibles selon le rôle
- `/payuser` : explique que les versements sortants ne sont disponibles que si un endpoint officiel autorisé est configuré; aucun transfert simulé.

## Webhook Pulse
Déploie `cloudflare-worker/worker.js` dans Cloudflare Workers. Ajoute le secret Pulse dans les **Secrets** du Worker sous `CHARIOW_PULSE_SECRET`. Configure l'URL Pulse pour viser `/chariow`. Le Worker vérifie la signature brute `x-chariow-signature` et écrit les livraisons dans son stockage durable avant d'accuser réception. Configure un Durable Object nommé `PulseStore` et lie-le comme `PULSE_STORE`. Sans stockage durable, ne mets pas les récompenses/confirmations en production.

## Sécurité / limites
- Ne mets pas de clé `sk_...`, token Telegram ou secret `whsec_...` dans GitHub.
- Ne fais pas confiance à un navigateur de retour ou à un bouton Telegram pour confirmer le paiement.
- La vérification du Pulse est obligatoire. Vérifie aussi l'ID de produit, le statut `completed`, la devise/montant attendu et le lien de commande avant toute livraison.
- La commande de paiement admin sortant n'envoie pas d'argent.
- Ce ZIP est un point de départ auditable, pas une garantie de compatibilité de tous les moyens de paiement/pays.
