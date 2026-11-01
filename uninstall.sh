#!/bin/sh
# uninstall.sh — desfaz o que o bootstrap.sh instalou nesta máquina, só dentro de $HOME, sem sudo.
#
#   ~/DEV/whitebeard-os/uninstall.sh              # remove serviços e programas; MANTÉM o second brain e secrets.env
#   ~/DEV/whitebeard-os/uninstall.sh --tudo       # remove também o second brain e os segredos (irreversível)
#
# O que fica sempre: o Claude Code e o seu login (são seus, não do Whitebeard OS), o uv e este clone do kernel.
# Rodar o install.sh de novo depois refaz tudo; com secrets.env mantido, o Telegram volta sem perguntas.
set -eu

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
TUDO=0
[ "${1:-}" = --tudo ] && TUDO=1
log() { printf '[whitebeard-os/uninstall] %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

vault=$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env" 2>/dev/null || true)
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
      docker compose --project-directory "$JARVIS_HOME/compose/knowledge" down -v >/dev/null 2>&1 && log "knowledge: containers e volumes removidos" || true
    fi ;;
esac
sleep 1
pkill -f "server/web.cjs" 2>/dev/null || true
pkill -f "$JARVIS_HOME/tools/" 2>/dev/null || true
pkill -f "hermes-agent/venv" 2>/dev/null || true

# ---- Claude Code: MCPs e skills que o kernel registrou (nunca os da pessoa)
if have claude; then
  for n in $(cat "$JARVIS_HOME/render/claude-mcp.names" 2>/dev/null); do
    claude mcp remove --scope user "$n" >/dev/null 2>&1 && log "claude: mcp $n removido" || true
  done
fi
for d in "$JARVIS_HOME"/skills/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  for link in "$HOME/.claude/skills/$n" "$HOME/.hermes/skills/$n"; do [ -L "$link" ] && rm -f "$link"; done
done

# ---- segredos e second brain
if [ "$TUDO" = 1 ]; then
  rm -f "$JARVIS_HOME/secrets.env"
  if [ -n "$vault" ] && [ -d "$vault" ]; then rm -rf "$vault"; log "second brain removido: $vault"; fi
else
  if [ -f "$JARVIS_HOME/secrets.env" ]; then
    mkdir -p "$HOME/.jarvis-mantido" && cp -p "$JARVIS_HOME/secrets.env" "$HOME/.jarvis-mantido/secrets.env" && chmod 600 "$HOME/.jarvis-mantido/secrets.env"
    log "secrets.env guardado em ~/.jarvis-mantido (o install.sh reaproveita)"
  fi
  [ -n "$vault" ] && [ -d "$vault" ] && log "second brain mantido: $vault (use --tudo para remover)"
fi
# a reserva de identidade do GBrain fica no diretório pai do vault; sem o cérebro ela só atrapalha
if [ -n "$vault" ]; then
  for f in "$(dirname "$vault")"/.gbrain-owner-*.json; do
    [ -f "$f" ] && python3 -c 'import json,os,sys; sys.exit(0 if os.path.realpath(json.load(open(sys.argv[1])).get("root",""))==os.path.realpath(sys.argv[2]) else 1)' "$f" "$vault" 2>/dev/null && rm -f "$f"
  done
fi

# ---- programas e dados do kernel
data=$(sed -n 's/^JARVIS_DATA=//p' "$JARVIS_HOME/profile.env" 2>/dev/null || true)
rm -rf "$JARVIS_HOME" "$HOME/.hermes" "$HOME/.paperclip" "$HOME/.gbrain" "$HOME/.nvm"
[ -n "$data" ] && [ "$TUDO" = 1 ] && rm -rf "$data"
rm -f "$HOME/.local/bin/ffmpeg" "$HOME/.local/bin/ffprobe" "$HOME/.local/bin/hermes" "$HOME/.local/bin/gbrain"
# atalho da loja gerenciada do Paperclip (paperclipai install): sem alvo, quebra o PATH
if [ -f "$HOME/.local/bin/paperclipai" ] && grep -q "managed install shim" "$HOME/.local/bin/paperclipai" 2>/dev/null; then rm -f "$HOME/.local/bin/paperclipai"; fi
for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.zprofile" "$HOME/.profile"; do
  [ -f "$rc" ] && grep -q '\.jarvis/profile\.sh' "$rc" && { grep -v '\.jarvis/profile\.sh' "$rc" > "$rc.tmp" && mv "$rc.tmp" "$rc"; }
done
# segredos mantidos voltam para o lugar: o próximo install.sh os encontra
if [ "$TUDO" != 1 ] && [ -f "$HOME/.jarvis-mantido/secrets.env" ]; then
  mkdir -p "$JARVIS_HOME" && mv "$HOME/.jarvis-mantido/secrets.env" "$JARVIS_HOME/secrets.env" && rmdir "$HOME/.jarvis-mantido" 2>/dev/null || true
fi
log "pronto. Mantidos: Claude Code e seu login, uv, este clone$( [ "$TUDO" = 1 ] || printf ', secrets.env e o second brain' )."
log "para instalar de novo: curl -fsSL https://raw.githubusercontent.com/whitebeardit/whitebeard-os/main/install.sh | sh"
