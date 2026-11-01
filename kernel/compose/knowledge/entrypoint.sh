#!/usr/bin/env bash
set -euo pipefail

mkdir -p /root/.gbrain
if [ ! -f /root/.gbrain/config.json ]; then
  cat > /root/.gbrain/config.json <<CFG
{
  "engine": "postgres",
  "database_url": "${DATABASE_URL}",
  "schema_pack": "gbrain-base-v2",
  "embedding_model": "ollama:${EMBED_MODEL:-bge-m3}",
  "embedding_dimensions": 1024
}
CFG
  chmod 600 /root/.gbrain/config.json
fi

# --no-autopilot-install: o autopilot instalaria um agendador no container; a sincronização do vault
# é responsabilidade do host.
gbrain apply-migrations --yes --no-autopilot-install

exec gbrain serve --http --port 3131 --bind 0.0.0.0 \
  --public-url "${GBRAIN_PUBLIC_URL:-http://localhost:3131}"
