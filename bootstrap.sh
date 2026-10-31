#!/bin/sh
# bootstrap.sh — instala um papel do Whitebeard OS num host zerado (Linux x86_64 ou macOS).
# Idempotente: pode rodar de novo a qualquer momento. Só escreve dentro de $HOME.
# Papel desta versão: desktop (Claude Code + skills + clientes MCP + Hermes opcional).
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
KERNEL_DIR="$SCRIPT_DIR/kernel"
# shellcheck source=kernel/versions.env
. "$KERNEL_DIR/versions.env"

ROLE="desktop"
TENANT=""
DRY_RUN=0
SKIP=","
WITH_HERMES=0
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
PATH="$HOME/.local/bin:$PATH"
export PATH

usage() {
  cat <<EOF
uso: $0 --tenant <jarvis.yaml> [opções]

  --tenant <arquivo>   declaração do tenant (obrigatório; ex.: tenants/exemplo/jarvis.yaml)
  --role <papel>       desktop (padrão). core, satelite e knowledge: ainda não implementados
  --home <dir>         raiz da instalação (padrão: \$JARVIS_HOME ou ~/.jarvis)
  --with-hermes        instala o Hermes upstream (tag $HERMES_TAG); opcional nesta versão
  --skip a,b           pula etapas: uv, node, claude, hermes, render, mcp, rc
  --dry-run            valida o jarvis.yaml e mostra o que faria; não escreve nada
  -h, --help           esta ajuda
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --role)        ROLE="$2"; shift 2 ;;
    --tenant)      TENANT="$2"; shift 2 ;;
    --home)        JARVIS_HOME="$2"; shift 2 ;;
    --skip)        SKIP=",$2,"; shift 2 ;;
    --with-hermes) WITH_HERMES=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) printf 'argumento desconhecido: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { printf '[whitebeard-os] %s\n' "$*"; }
die() { printf '[whitebeard-os] ERRO: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
skipped() { case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
run() {
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; else sh -c "$*"; fi
}

[ -n "$TENANT" ] || { usage >&2; die "--tenant é obrigatório"; }
[ -f "$TENANT" ] || die "tenant não encontrado: $TENANT"
[ "$ROLE" = "desktop" ] || die "papel '$ROLE' ainda não implementado nesta versão (só desktop)"

OS=$(uname -s)
ARCH=$(uname -m)
case "$OS" in
  Linux|Darwin) ;;
  *) die "sistema não suportado: $OS (alvos: Linux, macOS)" ;;
esac
case "$ARCH" in
  x86_64|arm64|aarch64) ;;
  *) die "arquitetura não suportada: $ARCH" ;;
esac
if [ "$OS" = Linux ] && [ "$ARCH" != x86_64 ]; then
  log "aviso: Linux $ARCH ainda não validado (alvo desta versão: x86_64)"
fi

check_prereqs() {
  missing=""
  for t in git curl python3; do have "$t" || missing="$missing $t"; done
  [ -z "$missing" ] && return 0
  if [ "$OS" = Darwin ]; then
    die "faltam:$missing. No macOS: xcode-select --install (traz git e python3)"
  fi
  die "faltam:$missing. No Ubuntu/Debian: sudo apt-get install -y git curl python3 ca-certificates"
}

install_uv() {
  skipped uv && { log "uv: pulado"; return 0; }
  if have uv && [ "$(uv --version 2>/dev/null | awk '{print $2}')" = "$UV_VERSION" ]; then
    log "uv $UV_VERSION já presente"; return 0
  fi
  log "instalando uv $UV_VERSION em ~/.local/bin"
  run "curl -LsSf 'https://astral.sh/uv/$UV_VERSION/install.sh' | env UV_INSTALL_DIR='$HOME/.local/bin' UV_NO_MODIFY_PATH=1 sh"
}

install_node() {
  skipped node && { log "node: pulado"; return 0; }
  NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  export NVM_DIR
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    log "instalando nvm $NVM_VERSION em $NVM_DIR"
    run "curl -fsSL 'https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh' | env PROFILE=/dev/null NVM_DIR='$NVM_DIR' bash"
  fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] nvm install %s\n' "$NODE_VERSION"; return 0; fi
  bash -c ". '$NVM_DIR/nvm.sh' >/dev/null 2>&1 && nvm install '$NODE_VERSION' >/dev/null && nvm alias default '$NODE_VERSION' >/dev/null && printf '[whitebeard-os] node %s pronto\n' \"\$(node -v)\""
}

install_claude() {
  skipped claude && { log "claude: pulado"; return 0; }
  if have claude && claude --version 2>/dev/null | grep -q "^$CLAUDE_CODE_VERSION"; then
    log "Claude Code $CLAUDE_CODE_VERSION já presente"; return 0
  fi
  log "instalando Claude Code $CLAUDE_CODE_VERSION"
  run "curl -fsSL https://claude.ai/install.sh | bash -s -- '$CLAUDE_CODE_VERSION'"
}

install_hermes() {
  if [ "$WITH_HERMES" != 1 ] || skipped hermes; then log "hermes: não solicitado (--with-hermes)"; return 0; fi
  HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
  export HERMES_HOME
  if have hermes || [ -x "$HERMES_HOME/bin/hermes" ]; then log "hermes já presente ($HERMES_HOME)"; return 0; fi
  log "instalando Hermes upstream $HERMES_TAG em $HERMES_HOME"
  run "curl -fsSL 'https://raw.githubusercontent.com/NousResearch/hermes-agent/$HERMES_TAG/setup-hermes.sh' | bash"
}

render_tenant() {
  skipped render && { log "render: pulado"; return 0; }
  if ! have uv; then
    if [ "$DRY_RUN" = 1 ]; then log "render: uv ainda ausente; o tenant será validado na execução real"; return 0; fi
    die "uv é necessário para renderizar o tenant (etapa uv pulada?)"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLE" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR" --dry-run
    return 0
  fi
  mkdir -p "$JARVIS_HOME"
  uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLE" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR"
}

apply_mcp() {
  skipped mcp && { log "mcp: pulado"; return 0; }
  script="$JARVIS_HOME/render/claude-mcp.sh"
  if [ "$DRY_RUN" = 1 ]; then log "mcp: aplicaria $script"; return 0; fi
  [ -f "$script" ] || { log "mcp: nada a aplicar"; return 0; }
  have claude || { log "mcp: claude ausente; pulei"; return 0; }
  sh "$script"
}

install_rc() {
  skipped rc && { log "rc: pulado"; return 0; }
  line=". \"$JARVIS_HOME/profile.sh\" # whitebeard-os"
  case "${SHELL:-/bin/sh}" in */zsh) rc="$HOME/.zshrc" ;; *) rc="$HOME/.bashrc" ;; esac
  seen=" "
  for f in "$rc" "$HOME/.zshrc" "$HOME/.bashrc"; do
    case "$seen" in *" $f "*) continue ;; esac
    seen="$seen$f "
    [ "$f" = "$rc" ] || [ -f "$f" ] || continue
    if [ -f "$f" ] && grep -qF '# whitebeard-os' "$f"; then continue; fi
    if [ "$DRY_RUN" = 1 ]; then log "rc: adicionaria a $f"; continue; fi
    printf '\n%s\n' "$line" >> "$f"
    log "rc: $f atualizado"
  done
}

doctor() {
  log "---- resumo ----"
  log "host: $OS $ARCH | tenant: $TENANT | papel: $ROLE | home: $JARVIS_HOME"
  if have uv; then log "uv: $(uv --version | awk '{print $2}')"; fi
  if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
    log "node: $(bash -c ". '${NVM_DIR:-$HOME/.nvm}/nvm.sh' >/dev/null 2>&1 && node -v")"
  fi
  if have claude; then log "claude: $(claude --version 2>/dev/null)"; fi
  if have hermes; then log "hermes: $(hermes --version 2>/dev/null | head -1)"; fi
  if [ -f "$JARVIS_HOME/render/summary.txt" ]; then cat "$JARVIS_HOME/render/summary.txt"; fi
  log "abra um terminal novo (ou: . \"$JARVIS_HOME/profile.sh\")"
}

log "whitebeard-os $(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo dev) | $OS/$ARCH | papel $ROLE$( [ "$DRY_RUN" = 1 ] && printf ' | dry-run' )"
check_prereqs
install_uv
install_node
install_claude
install_hermes
render_tenant
apply_mcp
install_rc
doctor
