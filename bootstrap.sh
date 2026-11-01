#!/bin/sh
# bootstrap.sh — instala papéis do Whitebeard OS num host zerado (Linux x86_64 ou macOS).
#   desktop    Claude Code + skills + clientes MCP + FFmpeg (+ Hermes CLI opcional)
#   core       Hermes (gateway Telegram + crons) e Paperclip como serviços do usuário
#   knowledge  GBrain + pgvector + Ollama em Docker Compose
# Idempotente: pode rodar de novo a qualquer momento. Só escreve dentro de $HOME.
# --no-start instala binários e renderiza configurações sem subir serviços nem containers (CI).
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
KERNEL_DIR="$SCRIPT_DIR/kernel"
# shellcheck source=kernel/versions.env
. "$KERNEL_DIR/versions.env"

ROLES=""
TENANT=""
DRY_RUN=0
NO_START=0
NO_PROMPT=0
KNOWLEDGE_ADIADO=0
SKIP=","
WITH_HERMES=""
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
PATH="$HOME/.local/bin:$PATH"
export PATH HERMES_HOME NVM_DIR

usage() {
  cat <<EOF
uso: $0 --tenant <jarvis.yaml> [opções]

  --tenant <arquivo>   declaração do tenant (obrigatório; ex.: tenants/exemplo/jarvis.yaml)
  --role a,b           papéis: desktop, core, knowledge. Padrão: os do host \`local\` em \`nos:\` do tenant (sem nos: os três, tudo-em-um)
  --home <dir>         raiz da instalação (padrão: \$JARVIS_HOME ou ~/.jarvis)
  --with-hermes        instala o Hermes upstream (tag $HERMES_TAG); padrão: sim no papel core, não no desktop
  --without-hermes     não instala o Hermes
  --no-start           instala e renderiza, mas não sobe serviços nem containers
  --skip a,b           pula etapas: uv, node, claude, ffmpeg, hermes, paperclip, render, mcp, web, knowledge, services, rc
  --dry-run            valida o jarvis.yaml e mostra o que faria; não escreve nada
  --no-prompt          não pergunta segredos no terminal (CI); deixa vazios em secrets.env
  -h, --help           esta ajuda
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --role)           ROLES="$2"; shift 2 ;;
    --tenant)         TENANT="$2"; shift 2 ;;
    --home)           JARVIS_HOME="$2"; shift 2 ;;
    --skip)           SKIP=",$2,"; shift 2 ;;
    --with-hermes)    WITH_HERMES=1; shift ;;
    --without-hermes) WITH_HERMES=0; shift ;;
    --no-start)       NO_START=1; shift ;;
    --dry-run)        DRY_RUN=1; shift ;;
    --no-prompt)      NO_PROMPT=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) printf 'argumento desconhecido: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { printf '[whitebeard-os] %s\n' "$*"; }
die() { printf '[whitebeard-os] ERRO: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
skipped() { case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
has_role() { case ",$ROLES," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
run() {
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; else sh -c "$*"; fi
}
with_node() { bash -c ". '$NVM_DIR/nvm.sh' >/dev/null 2>&1 && $1"; }

[ -n "$TENANT" ] || { usage >&2; die "--tenant é obrigatório"; }
[ -f "$TENANT" ] || die "tenant não encontrado: $TENANT"
# Sem --role: os papéis do host `local` em `nos:` do tenant; sem `nos:`, tudo-em-um (desktop,core,knowledge).
if [ -z "$ROLES" ]; then
  ROLES=$(python3 - "$TENANT" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
# leitura mínima sem yaml (o render valida de verdade): bloco `nos:` com host local
roles = []
m = re.search(r"^nos:\s*\n((?:[ \t-].*\n?)+)", txt, re.M)
if m:
    for item in re.split(r"\n\s*-\s", "\n" + m.group(1)):
        if not item.strip():
            continue
        host = re.search(r"host:\s*([^\s#]+)", item)
        if host and host.group(1) not in ("local", "localhost"):
            continue
        for key in ("papeis", "papel"):
            k = re.search(key + r":\s*\[([^\]]*)\]|" + key + r":\s*([^\s#\[]+)", item)
            if k:
                roles += [r.strip() for r in (k.group(1) or k.group(2)).split(",") if r.strip()]
print(",".join(dict.fromkeys(roles)) if roles else "desktop,core,knowledge")
PY
)
  log "papéis (de nos: do tenant): $ROLES"
fi
old_ifs=$IFS
IFS=,
for r in $ROLES; do
  case "$r" in
    desktop|core|knowledge) ;;
    satelite) die "papel satelite ainda não implementado" ;;
    *) die "papel desconhecido: $r (use desktop, core, knowledge)" ;;
  esac
done
IFS=$old_ifs
if [ -z "$WITH_HERMES" ]; then
  if has_role core; then WITH_HERMES=1; else WITH_HERMES=0; fi
fi

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
  for t in git curl python3 tar; do have "$t" || missing="$missing $t"; done
  if [ -n "$missing" ]; then
    if [ "$OS" = Darwin ]; then die "faltam:$missing. No macOS: xcode-select --install (traz git e python3)"; fi
    die "faltam:$missing. No Ubuntu/Debian: sudo apt-get install -y git curl python3 ca-certificates xz-utils"
  fi
  if has_role knowledge && ! skipped knowledge; then
    if ! have docker || ! docker compose version >/dev/null 2>&1; then
      # Docker pede senha de administrador para instalar: não dá para resolver aqui sem perguntar.
      # O papel knowledge fica de fora desta execução e os outros seguem; rode de novo com Docker.
      if [ "$OS" = Darwin ]; then
        log "aviso: o papel knowledge precisa de Docker (OrbStack ou Docker Desktop). Instale e rode o bootstrap de novo; seguindo sem knowledge"
      else
        log "aviso: o papel knowledge precisa de Docker com o plugin compose (https://docs.docker.com/engine/install/). Seguindo sem knowledge"
      fi
      ROLES=$(printf '%s' "$ROLES" | tr ',' '\n' | grep -vx knowledge | paste -sd, -)
      KNOWLEDGE_ADIADO=1
    fi
  fi
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
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    log "instalando nvm $NVM_VERSION em $NVM_DIR"
    run "curl -fsSL 'https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh' | env PROFILE=/dev/null NVM_DIR='$NVM_DIR' bash"
  fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] nvm install %s\n' "$NODE_VERSION"; return 0; fi
  with_node "nvm install '$NODE_VERSION' >/dev/null && nvm alias default '$NODE_VERSION' >/dev/null && printf '[whitebeard-os] node %s pronto\n' \"\$(node -v)\""
}

install_claude() {
  has_role desktop || { log "claude: só no papel desktop"; return 0; }
  skipped claude && { log "claude: pulado"; return 0; }
  if have claude && claude --version 2>/dev/null | grep -q "^$CLAUDE_CODE_VERSION"; then
    log "Claude Code $CLAUDE_CODE_VERSION já presente"; return 0
  fi
  log "instalando Claude Code $CLAUDE_CODE_VERSION"
  run "curl -fsSL https://claude.ai/install.sh | bash -s -- '$CLAUDE_CODE_VERSION'"
}

link_ffmpeg() {
  mkdir -p "$HOME/.local/bin"
  ln -sf "$1/ffmpeg" "$HOME/.local/bin/ffmpeg"
  if [ -x "$1/ffprobe" ]; then ln -sf "$1/ffprobe" "$HOME/.local/bin/ffprobe"; fi
  log "ffmpeg: $("$1/ffmpeg" -version 2>/dev/null | head -n 1 | cut -c1-60)"
  if "$1/ffmpeg" -hide_banner -filters 2>/dev/null | grep -q whisper; then
    log "ffmpeg: filtro whisper disponível"
  else
    log "ffmpeg: build sem filtro whisper (a transcrição segue pelo caminho atual)"
  fi
}

# Asset de release do kernel (repositório privado): via `gh` autenticado ou WHITEBEARD_OS_TOKEN (API do
# GitHub com Accept: application/octet-stream). Devolve 1 sem credencial ou sem o asset.
fetch_release_asset() {
  tag=$1; asset=$2; out=$3
  if have gh && gh auth status >/dev/null 2>&1; then
    gh release download "$tag" -R "$KERNEL_REPO_SLUG" -p "$asset" -O "$out" --clobber >/dev/null 2>&1 && return 0
  fi
  if [ -n "${WHITEBEARD_OS_TOKEN:-}" ]; then
    aurl=$(curl -fsSL -H "Authorization: Bearer $WHITEBEARD_OS_TOKEN" "https://api.github.com/repos/$KERNEL_REPO_SLUG/releases/tags/$tag" 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((a["url"] for a in d.get("assets",[]) if a["name"]==sys.argv[1]),""))' "$asset")
    [ -n "$aurl" ] && curl -fsSL -H "Authorization: Bearer $WHITEBEARD_OS_TOKEN" -H "Accept: application/octet-stream" "$aurl" -o "$out" && return 0
  fi
  return 1
}

sha256_of() { if have sha256sum; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# Build próprio com o filtro whisper, quando existe para a plataforma e dá para baixar; senão, o público.
install_ffmpeg_whisper() {
  case "$OS/$ARCH" in
    Linux/x86_64) tag="$FFMPEG_WHISPER_TAG_LINUX_X86_64"; asset="$FFMPEG_WHISPER_ASSET_LINUX_X86_64"; sha="$FFMPEG_WHISPER_SHA256_LINUX_X86_64" ;;
    *) return 1 ;;
  esac
  [ -n "$tag" ] && [ -n "$asset" ] || return 1
  dest="$JARVIS_HOME/tools/ffmpeg-whisper-${asset%.tar.xz}"
  if [ -f "$dest/.ok" ] && [ -x "$dest/ffmpeg" ]; then log "ffmpeg com whisper já presente"; link_ffmpeg "$dest"; return 0; fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] baixar release %s (%s) em %s, se houver credencial\n' "$tag" "$asset" "$dest"; return 0; fi
  mkdir -p "$dest"
  if ! fetch_release_asset "$tag" "$asset" "$dest/archive.tar.xz"; then
    rm -rf "$dest"; log "ffmpeg: build com whisper indisponível (sem gh autenticado nem WHITEBEARD_OS_TOKEN); usando o build público"; return 1
  fi
  got=$(sha256_of "$dest/archive.tar.xz")
  [ "$got" = "$sha" ] || { rm -rf "$dest"; die "ffmpeg com whisper: sha256 não confere ($got)"; }
  tar -xJf "$dest/archive.tar.xz" -C "$dest" && rm -f "$dest/archive.tar.xz"
  f=$(find "$dest" -type f -name ffmpeg | head -n 1); p=$(find "$dest" -type f -name ffprobe | head -n 1)
  [ -n "$f" ] || die "ffmpeg não encontrado no pacote com whisper"
  [ "$f" = "$dest/ffmpeg" ] || mv "$f" "$dest/ffmpeg"
  if [ -n "$p" ] && [ "$p" != "$dest/ffprobe" ]; then mv "$p" "$dest/ffprobe"; fi
  chmod +x "$dest/ffmpeg"; [ -f "$dest/ffprobe" ] && chmod +x "$dest/ffprobe"
  : > "$dest/.ok"
  log "ffmpeg: build com whisper instalado ($asset, sha256 conferido)"
  link_ffmpeg "$dest"
}

install_ffmpeg() {
  if ! has_role desktop && ! has_role core; then return 0; fi
  skipped ffmpeg && { log "ffmpeg: pulado"; return 0; }
  if install_ffmpeg_whisper; then return 0; fi
  case "$OS/$ARCH" in
    Linux/x86_64) url="$FFMPEG_URL_LINUX_X86_64" ;;
    Darwin/arm64) url="$FFMPEG_URL_DARWIN_ARM64" ;;
    *) log "ffmpeg: sem build fixado para $OS/$ARCH; fica o do sistema, se houver"; return 0 ;;
  esac
  dest="$JARVIS_HOME/tools/ffmpeg-$FFMPEG_VERSION"
  if [ -f "$dest/.ok" ] && [ -x "$dest/ffmpeg" ]; then
    log "ffmpeg $FFMPEG_VERSION já presente"; link_ffmpeg "$dest"; return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] baixar %s em %s\n' "$url" "$dest"; return 0; fi
  log "baixando ffmpeg $FFMPEG_VERSION ($OS/$ARCH)"
  rm -rf "$dest"
  mkdir -p "$dest"
  curl -fsSL "$url" -o "$dest/archive.tar.xz"
  tar -xJf "$dest/archive.tar.xz" -C "$dest"
  rm -f "$dest/archive.tar.xz"
  f=$(find "$dest" -type f -name ffmpeg | head -n 1)
  p=$(find "$dest" -type f -name ffprobe | head -n 1)
  [ -n "$f" ] || die "ffmpeg não encontrado no pacote baixado"
  if [ "$f" != "$dest/ffmpeg" ]; then mv "$f" "$dest/ffmpeg"; fi
  if [ -n "$p" ] && [ "$p" != "$dest/ffprobe" ]; then mv "$p" "$dest/ffprobe"; fi
  chmod +x "$dest/ffmpeg"
  if [ -f "$dest/ffprobe" ]; then chmod +x "$dest/ffprobe"; fi
  : > "$dest/.ok"
  link_ffmpeg "$dest"
}

install_hermes() {
  if [ "$WITH_HERMES" != 1 ] || skipped hermes; then log "hermes: não instalado (--with-hermes para incluir)"; return 0; fi
  if have hermes || [ -x "$HERMES_HOME/bin/hermes" ]; then log "hermes já presente ($HERMES_HOME)"; return 0; fi
  log "instalando Hermes upstream $HERMES_TAG em $HERMES_HOME"
  # O setup-hermes.sh upstream trabalha relativo à própria pasta (venv/, uv sync): precisa rodar de dentro
  # de um checkout do hermes-agent, nunca do kernel. As duas perguntas dele (ripgrep, assistente de
  # configuração) recebem "n": o tenant já traz a configuração e o assistente é interativo.
  dir="$HERMES_HOME/hermes-agent"
  if [ ! -d "$dir/.git" ]; then
    mkdir -p "$HERMES_HOME"
    git clone -q --depth 1 --branch "$HERMES_TAG" https://github.com/NousResearch/hermes-agent.git "$dir" || die "hermes: clone do hermes-agent falhou"
  fi
  (cd "$dir" && printf 'nn' | bash setup-hermes.sh) || die "hermes: instalador upstream falhou (veja a saída acima)"
  have hermes || [ -x "$HOME/.local/bin/hermes" ] || die "hermes: comando não encontrado depois da instalação"
}

install_paperclip() {
  has_role core || return 0
  skipped paperclip && { log "paperclip: pulado"; return 0; }
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] npm install -g paperclipai@%s\n' "$PAPERCLIP_VERSION"; return 0; fi
  if with_node "npm ls -g 'paperclipai@$PAPERCLIP_VERSION' >/dev/null 2>&1"; then
    log "paperclip $PAPERCLIP_VERSION já presente"
  else
    log "instalando paperclip $PAPERCLIP_VERSION"
    with_node "npm install -g --no-fund --no-audit 'paperclipai@$PAPERCLIP_VERSION' >/dev/null"
  fi
}

render_tenant() {
  skipped render && { log "render: pulado"; return 0; }
  if ! have uv; then
    if [ "$DRY_RUN" = 1 ]; then log "render: uv ainda ausente; o tenant será validado na execução real"; return 0; fi
    die "uv é necessário para renderizar o tenant (etapa uv pulada?)"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLES" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR" --dry-run
    return 0
  fi
  mkdir -p "$JARVIS_HOME"
  uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLES" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR"
  ask_secrets && uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLES" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR" >/dev/null
  return 0
}

# Segredos obrigatórios ainda vazios: pergunta no terminal (pelo /dev/tty, que funciona mesmo com
# `curl | sh`), grava em secrets.env e devolve 0 para o render rodar de novo. Sem terminal, só avisa.
ask_secrets() {
  f="$JARVIS_HOME/render/secrets-missing.txt"
  [ -s "$f" ] || return 1
  # sem terminal de verdade (ssh sem -t, cron, CI) o /dev/tty existe mas não abre: só avisa
  if [ "$NO_PROMPT" = 1 ] || ! ( : </dev/tty ) 2>/dev/null; then
    log "segredos obrigatórios vazios: $(paste -sd' ' "$f"). Preencha $JARVIS_HOME/secrets.env e rode de novo"
    return 1
  fi
  log "segredos obrigatórios para os papéis $ROLES (Enter em branco para deixar para depois):"
  answered=0
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    hint=$(grep -B4 "^$name=" "$JARVIS_HOME/secrets.env" | grep '^# como obter:' | tail -n 1 | sed 's/^# como obter: //')
    printf '  %s%s: ' "$name" "${hint:+ ($hint)}" >/dev/tty
    stty -echo </dev/tty 2>/dev/null || true
    IFS= read -r value </dev/tty
    stty echo </dev/tty 2>/dev/null || true
    printf '\n' >/dev/tty
    [ -n "$value" ] || continue
    python3 - "$JARVIS_HOME/secrets.env" "$name" "$value" <<'PY'
import re, sys
p, name, value = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
q = "'" + value.replace("'", "'\\''") + "'"
s2, n = re.subn(r"(?m)^" + re.escape(name) + r"=.*$", name + "=" + q, s)
open(p, "w", encoding="utf-8").write(s2 if n else s + "\n" + name + "=" + q + "\n")
PY
    answered=1
  done < "$f"
  [ "$answered" = 1 ]
}

apply_mcp() {
  has_role desktop || return 0
  skipped mcp && { log "mcp: pulado"; return 0; }
  script="$JARVIS_HOME/render/claude-mcp.sh"
  if [ "$DRY_RUN" = 1 ]; then log "mcp: aplicaria $script"; return 0; fi
  [ -f "$script" ] || { log "mcp: nada a aplicar"; return 0; }
  have claude || { log "mcp: claude ausente; pulei"; return 0; }
  sh "$script"
}

apply_hermes_config() {
  [ "$WITH_HERMES" = 1 ] || return 0
  [ "$DRY_RUN" = 1 ] && return 0
  [ -d "$JARVIS_HOME/render/hermes" ] || return 0
  mkdir -p "$HERMES_HOME"
  if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "$JARVIS_HOME/render/hermes/config.yaml" ]; then
    cp "$JARVIS_HOME/render/hermes/config.yaml" "$HERMES_HOME/config.yaml"
    log "hermes: config.yaml inicial escrito (modelo do tenant)"
  fi
  if [ -f "$JARVIS_HOME/render/hermes/env" ]; then
    touch "$HERMES_HOME/.env"
    chmod 600 "$HERMES_HOME/.env"
    while IFS= read -r line; do
      case "$line" in ''|'#'*) continue ;; esac
      k=${line%%=*}
      if ! grep -q "^$k=" "$HERMES_HOME/.env"; then printf '%s\n' "$line" >> "$HERMES_HOME/.env"; fi
    done < "$JARVIS_HOME/render/hermes/env"
    log "hermes: variáveis do tenant mescladas em $HERMES_HOME/.env (sem sobrescrever)"
  fi
}

install_knowledge() {
  has_role knowledge || return 0
  skipped knowledge && { log "knowledge: pulado"; return 0; }
  dest="$JARVIS_HOME/compose/knowledge"
  if [ "$DRY_RUN" = 1 ]; then log "knowledge: copiaria o compose para $dest e validaria com docker compose"; return 0; fi
  mkdir -p "$dest"
  for f in compose.yml compose.gpu.yml Dockerfile.gbrain entrypoint.sh; do cp "$KERNEL_DIR/compose/knowledge/$f" "$dest/$f"; done
  [ -f "$JARVIS_HOME/render/knowledge.env" ] || die "knowledge.env não renderizado (etapa render pulada?)"
  cp "$JARVIS_HOME/render/knowledge.env" "$dest/.env"
  chmod 600 "$dest/.env"
  files="-f $dest/compose.yml"
  accel=$(sed -n 's/^ACELERACAO=//p' "$dest/.env")
  if [ "$accel" = auto ] && [ "$OS" = Linux ] && have nvidia-smi; then accel=cuda; fi
  if [ "$accel" = cuda ]; then
    if docker info 2>/dev/null | grep -qi nvidia; then
      files="$files -f $dest/compose.gpu.yml"; log "knowledge: GPU NVIDIA ligada no Ollama"
    else
      log "knowledge: GPU pedida, mas o Docker não tem o runtime NVIDIA; seguindo em CPU"
    fi
  fi
  # shellcheck disable=SC2086
  docker compose --project-directory "$dest" $files config -q && log "knowledge: compose válido em $dest"
  if [ "$NO_START" = 1 ]; then
    log "knowledge: --no-start; para subir: docker compose --project-directory $dest up -d --build"
    return 0
  fi
  # shellcheck disable=SC2086
  docker compose --project-directory "$dest" $files up -d --build
  log "knowledge: GBrain em http://localhost:$(sed -n 's/^GBRAIN_PORT=//p' "$dest/.env") (MCP em /mcp); Ollama baixando $EMBED_MODEL"
}

install_services() {
  has_role core || return 0
  skipped services && { log "serviços: pulados"; return 0; }
  if [ "$DRY_RUN" = 1 ] || [ "$NO_START" = 1 ]; then
    log "serviços: não instalados (--no-start). Depois: paperclipai onboard -y --bind lan --install-service; hermes gateway install --start-on-login"
    return 0
  fi
  if [ ! -f "$HOME/.paperclip/instances/default/config.json" ]; then
    log "paperclip: primeira configuração (rede local) e serviço de usuário"
    with_node "paperclipai onboard -y --bind lan --install-service </dev/null"
  else
    with_node "paperclipai service install --json >/dev/null 2>&1 || true; paperclipai service start >/dev/null 2>&1 || true"
    log "paperclip: serviço presente e iniciado"
  fi
  if [ "$WITH_HERMES" = 1 ] && have hermes; then
    if grep -q '^TELEGRAM_BOT_TOKEN=.' "$HERMES_HOME/.env" 2>/dev/null; then
      hermes gateway install --if-missing --start-on-login --start-now && log "hermes: gateway instalado como serviço"
    else
      log "hermes: sem TELEGRAM_BOT_TOKEN em $HERMES_HOME/.env; gateway não instalado (preencha secrets.env e rode de novo)"
    fi
  fi
}

# JARVIS Web: a interface do produto. Clona o app na ref do lockfile, instala só as dependências de
# runtime (sem Electron), constrói a UI e registra um serviço de usuário que lê $JARVIS_HOME/profile.env.
install_web() {
  has_role desktop || return 0
  skipped web && { log "web: pulado"; return 0; }
  app="$JARVIS_HOME/app/jarvis-web"
  if [ "$DRY_RUN" = 1 ]; then
    log "web: clonaria $JARVIS_WEB_REPO ($JARVIS_WEB_REF) em $app, npm ci sem dev, vite build, serviço jarvis-web na porta $JARVIS_WEB_PORT"
    return 0
  fi
  [ -s "$NVM_DIR/nvm.sh" ] || die "web: Node ausente (etapa node pulada?)"
  if [ -d "$app/.git" ]; then
    git -C "$app" fetch -q --depth 1 origin "$JARVIS_WEB_REF" || die "web: não consegui atualizar $app"
    git -C "$app" checkout -q --detach FETCH_HEAD
  else
    mkdir -p "$JARVIS_HOME/app"
    git clone -q --depth 1 --branch "$JARVIS_WEB_REF" "$JARVIS_WEB_REPO" "$app" || die "web: clone falhou ($JARVIS_WEB_REPO). Chave SSH com acesso ao repositório do app?"
  fi
  log "web: dependências de runtime e build da UI (sem Electron)"
  with_node "cd '$app' && NPM_CONFIG_UPDATE_NOTIFIER=false npm ci --omit=dev --no-audit --no-fund --loglevel=error && npx vite build --logLevel error" || die "web: build falhou"
  node_bin=$(with_node 'command -v node')
  install_web_service "$app" "$node_bin"
}

# Certificado autoassinado para o JARVIS Web na LAN (o microfone no celular exige HTTPS). Gerado uma
# vez em $JARVIS_HOME/tls com SAN para localhost, o nome do host e os IPs atuais; o celular confia nele
# instalando o .crt (docs/validacao-macos.md). Sem openssl, o serviço fica em HTTP e o bootstrap avisa.
ensure_web_tls() {
  tls_dir="$JARVIS_HOME/tls"
  # TLS_GEN muda quando a forma de gerar muda: o certificado antigo é refeito uma vez
  TLS_GEN=3
  if [ -s "$tls_dir/jarvis-web.crt" ] && [ -s "$tls_dir/jarvis-web.key" ] && [ "$(cat "$tls_dir/GEN" 2>/dev/null)" = "$TLS_GEN" ]; then return 0; fi
  have openssl || { log "web: openssl ausente; JARVIS Web fica em HTTP (sem microfone fora do localhost)"; return 1; }
  mkdir -p "$tls_dir"
  san="DNS:localhost,DNS:$(hostname),IP:127.0.0.1"
  # só IPs de rede local (RFC 1918): endereços de VPN ou túnel são transitórios e não entram no SAN
  for ip in $(hostname -I 2>/dev/null || ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2}'); do
    case "$ip" in 10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) san="$san,IP:$ip" ;; esac
  done
  # 820 dias: Safari e iOS recusam certificados de servidor com mais de 825 dias, mesmo confiados à mão.
  # ec_param_enc:named_curve é obrigatório: o LibreSSL do macOS gera a chave EC com parâmetros explícitos,
  # que Chrome (BoringSSL) e o próprio LibreSSL rejeitam no handshake TLS 1.3 ("decode error").
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -pkeyopt ec_param_enc:named_curve -nodes -sha256 -days 820 \
    -subj "/CN=jarvis-web" -addext "subjectAltName=$san" \
    -keyout "$tls_dir/jarvis-web.key" -out "$tls_dir/jarvis-web.crt" >/dev/null 2>&1 || { log "web: falha ao gerar certificado; fica em HTTP"; return 1; }
  chmod 600 "$tls_dir/jarvis-web.key"
  echo "$TLS_GEN" >"$tls_dir/GEN"
  san_real=$(openssl x509 -in "$tls_dir/jarvis-web.crt" -noout -ext subjectAltName 2>/dev/null | tail -n 1 | sed 's/^ *//')
  log "web: certificado TLS gerado em $tls_dir (${san_real:-SAN: $san}; válido 820 dias)"
}

install_web_service() {
  app=$1
  node_bin=$2
  tls_env=""
  tls_xml=""
  proto=http
  if ensure_web_tls; then
    proto=https
    tls_env="Environment=JARVIS_WEB_TLS_CERT=$JARVIS_HOME/tls/jarvis-web.crt
Environment=JARVIS_WEB_TLS_KEY=$JARVIS_HOME/tls/jarvis-web.key"
    tls_xml="      <key>JARVIS_WEB_TLS_CERT</key><string>$JARVIS_HOME/tls/jarvis-web.crt</string>
      <key>JARVIS_WEB_TLS_KEY</key><string>$JARVIS_HOME/tls/jarvis-web.key</string>"
  fi
  case "$OS" in
    Linux)
      unit_dir="$HOME/.config/systemd/user"
      mkdir -p "$unit_dir"
      cat >"$unit_dir/jarvis-web.service" <<EOF
[Unit]
Description=JARVIS Web (Whitebeard OS)
After=network.target

[Service]
EnvironmentFile=$JARVIS_HOME/profile.env
Environment=JARVIS_WEB_PORT=$JARVIS_WEB_PORT
$tls_env
WorkingDirectory=$app
ExecStart=$node_bin $app/server/web.cjs
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
      if [ "$NO_START" = 1 ]; then
        log "web: unidade escrita em $unit_dir/jarvis-web.service (--no-start). Depois: systemctl --user enable --now jarvis-web"
        return 0
      fi
      systemctl --user daemon-reload && systemctl --user enable --now jarvis-web.service && log "web: serviço jarvis-web ativo em $proto://localhost:$JARVIS_WEB_PORT"
      ;;
    Darwin)
      plist="$HOME/Library/LaunchAgents/dev.whitebeard.jarvis-web.plist"
      mkdir -p "$HOME/Library/LaunchAgents" "$JARVIS_HOME/logs"
      env_xml=$(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=\(.*\)$/      <key>\1<\/key><string>\2<\/string>/p' "$JARVIS_HOME/profile.env")
      cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.whitebeard.jarvis-web</string>
  <key>ProgramArguments</key><array><string>$node_bin</string><string>$app/server/web.cjs</string></array>
  <key>WorkingDirectory</key><string>$app</string>
  <key>EnvironmentVariables</key><dict>
$env_xml
      <key>JARVIS_WEB_PORT</key><string>$JARVIS_WEB_PORT</string>
$tls_xml
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$JARVIS_HOME/logs/jarvis-web.log</string>
  <key>StandardErrorPath</key><string>$JARVIS_HOME/logs/jarvis-web.log</string>
</dict>
</plist>
EOF
      if [ "$NO_START" = 1 ]; then
        log "web: LaunchAgent escrito em $plist (--no-start). Depois: launchctl bootstrap gui/\$(id -u) $plist"
        return 0
      fi
      launchctl bootout "gui/$(id -u)/dev.whitebeard.jarvis-web" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$plist" && log "web: LaunchAgent ativo em $proto://localhost:$JARVIS_WEB_PORT"
      ;;
  esac
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
  log "host: $OS $ARCH | tenant: $TENANT | papéis: $ROLES | home: $JARVIS_HOME"
  if have uv; then log "uv: $(uv --version | awk '{print $2}')"; fi
  if [ -s "$NVM_DIR/nvm.sh" ]; then log "node: $(with_node 'node -v')"; fi
  if have claude; then log "claude: $(claude --version 2>/dev/null)"; fi
  if have ffmpeg; then log "ffmpeg: $(ffmpeg -version 2>/dev/null | head -n 1 | awk '{print $3}')"; fi
  if have hermes; then log "hermes: $(hermes --version 2>/dev/null | head -1)"; fi
  if has_role core && [ -s "$NVM_DIR/nvm.sh" ]; then log "paperclip: $(with_node 'paperclipai -V 2>/dev/null' | tail -n 1)"; fi
  if has_role knowledge && have docker; then log "knowledge: $(docker compose --project-directory "$JARVIS_HOME/compose/knowledge" ps --format '{{.Name}}={{.State}}' 2>/dev/null | tr '\n' ' ')"; fi
  if has_role desktop && [ -f "$JARVIS_HOME/app/jarvis-web/dist/index.html" ]; then
    p=http; [ -s "$JARVIS_HOME/tls/jarvis-web.crt" ] && p=https
    log "web: JARVIS Web em $p://localhost:$JARVIS_WEB_PORT (token em \$JARVIS_DATA/jarvis-web.json; certificado em \$JARVIS_HOME/tls)"
  fi
  if [ -f "$JARVIS_HOME/render/summary.txt" ]; then cat "$JARVIS_HOME/render/summary.txt"; fi
  [ "$KNOWLEDGE_ADIADO" = 1 ] && log "knowledge: adiado (sem Docker). Instale Docker e rode o bootstrap de novo"
  log "abra um terminal novo (ou: . \"$JARVIS_HOME/profile.sh\")"
}

log "whitebeard-os $(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo dev) | $OS/$ARCH | papéis $ROLES$( [ "$DRY_RUN" = 1 ] && printf ' | dry-run' )$( [ "$NO_START" = 1 ] && printf ' | no-start' )"
check_prereqs
install_uv
install_node
install_claude
install_ffmpeg
install_hermes
install_paperclip
render_tenant
apply_mcp
apply_hermes_config
install_knowledge
install_web
install_services
install_rc
doctor
