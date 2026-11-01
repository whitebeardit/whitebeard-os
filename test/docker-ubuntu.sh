#!/bin/sh
# Roda a validação do papel desktop numa Ubuntu 24.04 descartável, como usuário comum.
# Não toca no host: o container morre no fim. Equivalente local do job de CI em Linux.
set -eu
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 bash -c '
  set -e
  apt-get update -qq >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl python3 ca-certificates xz-utils >/dev/null
  useradd -m -s /bin/bash t
  su - t -c "
    set -e
    cp -r /src ~/whitebeard-os
    cd ~/whitebeard-os
    ./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --role desktop,core --dry-run
    ./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --role desktop,core --no-start
    ./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --role desktop,core --no-start
    . ~/.jarvis/profile.sh
    test \"\$JARVIS_TENANT\" = exemplo
    node -v | grep -q \"^v24.22.2\$\"
    ffmpeg -version | head -n 1
    paperclipai -V
    claude --version
    test -L ~/.claude/skills/exemplo-ola
    test \"\$(stat -c %a ~/.jarvis/secrets.env)\" = 600
    claude mcp get exemplo_knowledge
    claude mcp get exemplo_ferramentas
    grep -c \"# whitebeard-os\" ~/.bashrc | grep -qx 1
    echo OK-ubuntu-24.04
  "
'
