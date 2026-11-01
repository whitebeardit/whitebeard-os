#!/bin/sh
# uninstall.sh — desfaz o que o bootstrap.sh instalou nesta máquina, só dentro de $HOME, sem sudo.
#
#   ~/DEV/whitebeard-os/uninstall.sh          # para e remove serviços e programas do kernel; MANTÉM os dados
#   ~/DEV/whitebeard-os/uninstall.sh --delete-data   # remove também os dados (irreversível)
#
# Dados que o modo padrão mantém: o second brain, secrets.env, a pasta de dados do tenant (token do JARVIS
# Web, board key), o Paperclip (~/.paperclip: empresas, agentes), o Hermes (~/.hermes: configuração,
# sessões) e o cérebro (~/.gbrain). Node/nvm fica sempre, a menos que o kernel o tenha instalado e --delete-data.
# Nunca tocados: o Claude Code e o seu login, o uv, este clone do kernel.
# Rodar o install.sh de novo depois refaz tudo; com secrets.env mantido, nada é perguntado.
set -eu

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
DELETE_DATA=0
[ "${1:-}" = --delete-data ] && DELETE_DATA=1
log() { printf '[whitebeard-os/uninstall] %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

vault=$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env" 2>/dev/null || true)
data=$(sed -n 's/^JARVIS_DATA=//p' "$JARVIS_HOME/profile.env" 2>/dev/null || true)
PATH="$HOME/.local/bin:$PATH"; export PATH
[ -s "$HOME/.nvm/nvm.sh" ] && { NVM_DIR="$HOME/.nvm"; export NVM_DIR; . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true; }

# ---- serviços
log "parando serviços"
have paperclipai && paperclipai service stop >/dev/null 2>&1 || true
have hermes && hermes gateway stop >/dev/null 2>&1 || true
case "$(uname -s)" in
  Darwin)
    for l in dev.whitebeard.jarvis-web dev.whitebeard.ollama dev.whitebeard.gbrain dev.whitebeard.vault-sync ai.hermes.gateway ing.paperclip.paperclipai; do
      launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
      rm -f "$HOME/Library/LaunchAgents/$l.plist"
    done ;;
  Linux)
    for u in jarvis-web hermes-gateway paperclip; do systemctl --user disable --now "$u.service" 2>/dev/null || true; rm -f "$HOME/.config/systemd/user/$u.service"; done
    systemctl --user daemon-reload 2>/dev/null || true
    if have docker && [ -d "$JARVIS_HOME/compose/knowledge" ]; then
      if [ "$DELETE_DATA" = 1 ]; then
        docker compose --project-directory "$JARVIS_HOME/compose/knowledge" down -v >/dev/null 2>&1 && log "knowledge: containers e volumes removidos" || true
      else
        docker compose --project-directory "$JARVIS_HOME/compose/knowledge" down >/dev/null 2>&1 && log "knowledge: containers parados (volumes mantidos)" || true
      fi
    fi ;;
esac
sleep 1
pkill -f "server/web.cjs" 2>/dev/null || true
pkill -f "$JARVIS_HOME/tools/" 2>/dev/null || true

# ---- Claude Code e Hermes: MCPs e skills que o kernel registrou (nunca os da pessoa)
if have claude; then
  for n in $(cat "$JARVIS_HOME/render/claude-mcp.names" 2>/dev/null); do
    claude mcp remove --scope user "$n" >/dev/null 2>&1 && log "claude: mcp $n removido" || true
  done
  for n in $(cat "$JARVIS_HOME/render/claude-plugins.names" 2>/dev/null); do
    claude plugin uninstall "$n" </dev/null >/dev/null 2>&1 && log "claude: plugin $n desinstalado" || true
  done
fi
for d in "$JARVIS_HOME"/skills/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  for link in "$HOME/.claude/skills/$n" "$HOME/.hermes/skills/$n"; do [ -L "$link" ] && rm -f "$link"; done
done

# ---- o que se guarda do $JARVIS_HOME antes de removê-lo
keep=$(mktemp -d)
[ -f "$JARVIS_HOME/.nvm-instalado-pelo-kernel" ] && : > "$keep/.nvm-instalado-pelo-kernel"
if [ "$DELETE_DATA" != 1 ]; then
  [ -f "$JARVIS_HOME/secrets.env" ] && cp -p "$JARVIS_HOME/secrets.env" "$keep/secrets.env"
  # pasta de dados padrão mora dentro de $JARVIS_HOME; a do tenant (dados:) mora fora e não é tocada
  case "$data" in "$JARVIS_HOME"/*) [ -d "$data" ] && cp -Rp "$data" "$keep/data" ;; esac
fi

# ---- programas e configuração do kernel
rm -rf "$JARVIS_HOME"
rm -f "$HOME/.local/bin/ffmpeg" "$HOME/.local/bin/ffprobe" "$HOME/.local/bin/hermes" "$HOME/.local/bin/gbrain"
# atalho da loja gerenciada do Paperclip (paperclipai install) que ficaria sem alvo
if [ "$DELETE_DATA" = 1 ] && [ -f "$HOME/.local/bin/paperclipai" ] && grep -q "managed install shim" "$HOME/.local/bin/paperclipai" 2>/dev/null; then rm -f "$HOME/.local/bin/paperclipai"; fi
# linha do profile nos rc (marcador "# whitebeard-os", o mesmo que o install_rc escreve)
for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.zprofile" "$HOME/.profile"; do
  [ -f "$rc" ] || continue
  if grep -qF '# whitebeard-os' "$rc" || grep -qF "$JARVIS_HOME/profile.sh" "$rc"; then
    python3 - "$rc" "$JARVIS_HOME" <<'PY'
import sys
p, home = sys.argv[1:3]
lines = open(p, encoding="utf-8").read().split("\n")
out = [l for l in lines if "# whitebeard-os" not in l and f"{home}/profile.sh" not in l]
open(p, "w", encoding="utf-8").write("\n".join(out))
PY
    log "rc: linha do Whitebeard OS removida de $rc"
  fi
done

# ---- dados
if [ "$DELETE_DATA" = 1 ]; then
  rm -rf "$HOME/.hermes" "$HOME/.paperclip" "$HOME/.gbrain"
  [ -n "$data" ] && rm -rf "$data"
  if [ -n "$vault" ] && [ -d "$vault" ]; then rm -rf "$vault"; log "second brain removido: $vault"; fi
  # a reserva de identidade do GBrain fica no diretório pai do vault
  if [ -n "$vault" ]; then
    for f in "$(dirname "$vault")"/.gbrain-owner-*.json; do
      [ -f "$f" ] && python3 -c 'import json,os,sys; sys.exit(0 if os.path.realpath(json.load(open(sys.argv[1])).get("root",""))==os.path.realpath(sys.argv[2]) else 1)' "$f" "$vault" 2>/dev/null && rm -f "$f"
    done
  fi
  [ -f "$keep/.nvm-instalado-pelo-kernel" ] && rm -rf "$HOME/.nvm"
  log "removido tudo: dados, second brain, Paperclip, Hermes e cérebro"
else
  mkdir -p "$JARVIS_HOME"
  [ -f "$keep/secrets.env" ] && cp -p "$keep/secrets.env" "$JARVIS_HOME/secrets.env"
  [ -d "$keep/data" ] && cp -Rp "$keep/data" "$data"
  log "mantidos: secrets.env$( [ -n "$data" ] && printf ', dados em %s' "$data" ), ~/.paperclip, ~/.hermes, ~/.gbrain$( [ -n "$vault" ] && printf ', second brain em %s' "$vault" ) (use --delete-data para remover)"
fi
rm -rf "$keep"
log "pronto. Nunca tocados: Claude Code e seu login, uv, este clone."
log "para instalar de novo: curl -fsSL https://raw.githubusercontent.com/whitebeardit/whitebeard-os/main/install.sh | sh"
