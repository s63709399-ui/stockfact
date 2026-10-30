# StockFact – Docker (PostgreSQL + Python + Node.js)

## Démarrage
    cp .env.example .env     # puis renseignez POSTGRES_PASSWORD et GEMINI_API_KEY (gratuit) ou ANTHROPIC_API_KEY
    docker compose up -d --build
Ouvrez http://localhost:3000

## Mise à jour d'une installation existante (les données sont conservées)
1. Sauvegarde : docker compose exec db pg_dump -U stockfact stockfact > sauvegarde.sql
2. Copiez les nouveaux fichiers DANS LE MÊME DOSSIER (ne changez pas le nom du dossier : le volume des données en dépend).
3. Ajoutez GEMINI_API_KEY=... (gratuit, aistudio.google.com/apikey) dans votre fichier .env
4. docker compose up -d --build

## Commandes utiles
    docker compose logs -f api
    docker compose down                 # arrêter (données conservées)
    docker compose down -v              # arrêter ET effacer la base
    docker compose exec -T db psql -U stockfact stockfact < sauvegarde.sql   # restauration

## Architecture
- db  : PostgreSQL 16 (volume pgdata)
- api : Python / FastAPI (produits, clients, factures, versements, fournisseurs, achats…) + lecture des factures par photo (/api/scan)
- web : Node.js / Express (interface + proxy /api)
