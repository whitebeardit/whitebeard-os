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
WITH_TELEGRAM=0
PAPERCLIP_LOCAL=0
# o knowledge gerou um segredo novo (token do MCP): render e MCPs precisam ser reaplicados
RERENDER=0
HERMES_RESTART=0
KNOWLEDGE_ADIADO=0
# knowledge no tudo-em-um do macOS é nativo (sem Docker); no Linux segue o compose
KNOWLEDGE_NATIVE=0
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
  --skip a,b           pula etapas: uv, node, claude, ffmpeg, hermes, paperclip, render, mcp, plugins, web, knowledge, services, rc
  --dry-run            valida o jarvis.yaml e mostra o que faria; não escreve nada
  --no-prompt          não pergunta segredos no terminal (CI); deixa vazios em secrets.env
  --telegram           liga o Telegram: pede o token do bot e reconhece o seu chat pela primeira mensagem
  --paperclip-local    migra um Paperclip já instalado (autenticado, na rede) para só esta máquina, sem login
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
    --telegram)       WITH_TELEGRAM=1; shift ;;
    --paperclip-local) PAPERCLIP_LOCAL=1; shift ;;
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
# Leitura mínima de `nos:` sem yaml (o render valida de verdade). Esta máquina é o nó `local`/`localhost`
# ou o nó cujo host é o seu nome: nome curto casa com nome curto, nome com domínio só com o FQDN igual.
#   nos_query roles   → papéis do nó desta máquina ("" se nenhum nó casa; "-" se o tenant não tem nos:)
#   nos_query others  → quantos nós de nos: são outras máquinas
HOST_FULL=$(hostname 2>/dev/null | tr '[:upper:]' '[:lower:]')
nos_query() {
  python3 - "$TENANT" "$HOST_FULL" "$1" <<'PY'
import re, sys
txt, host, mode = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2], sys.argv[3]
short = host.split(".")[0]
def is_me(name):
    name = name.strip("\"'").lower()
    if name in ("local", "localhost"):
        return True
    return name == host if "." in name else name == short
m = re.search(r"^nos:\s*\n((?:[ \t-].*\n?)+)", txt, re.M)
if not m:
    print("-" if mode == "roles" else "0"); sys.exit(0)
# comentários fora; cada nó começa numa linha "- host:" (ou "- papeis:", quando o host vem depois)
block = re.sub(r"#.*", "", m.group(1))
roles, others = [], 0
for item in re.split(r"\n(?=\s*-\s+\w+:)", "\n" + block):
    if not item.strip():
        continue
    h = re.search(r"host:\s*(\S+)", item)
    if h and not is_me(h.group(1)):
        others += 1
        continue
    for key in ("papeis", "papel"):
        # em linha ([a, b]) ou valor único na mesma linha; o valor único não atravessa a quebra de linha
        # (senão o "-" da lista em bloco viraria um papel)
        k = re.search(key + r":[ \t]*\[([^\]]*)\]|" + key + r":[ \t]*([^\s\[-][^\s\[]*)", item)
        if k:
            roles += [r.strip().strip("\"'") for r in (k.group(1) or k.group(2)).split(",") if r.strip()]
        elif re.search(key + r":[ \t]*$", item, re.M):
            # lista em bloco: as linhas "- x" depois da chave (a linha "- host:" não conta)
            tail = item.split(key + ":", 1)[1]
            roles += [r.strip("\"'") for r in re.findall(r"^\s*-\s+([^\s:]+)\s*$", tail, re.M)]
print(",".join(dict.fromkeys(roles)) if mode == "roles" else others)
PY
}
# Sem --role: os papéis do nó desta máquina. Sem `nos:`, tudo-em-um (desktop,core,knowledge). Com `nos:` mas
# sem nó que seja esta máquina, é erro: instalar os três papéis num servidor de produção por engano é pior
# do que parar e pedir --role.
if [ -z "$ROLES" ]; then
  ROLES=$(nos_query roles)
  case "$ROLES" in
    -) ROLES="desktop,core,knowledge"; log "papéis: tudo-em-um (o tenant não declara nos:)" ;;
    "") die "nenhum nó em nos: do tenant é esta máquina (hostname $HOST_FULL). Acrescente-a em nos: ou informe --role" ;;
    *) log "papéis (nó $HOST_FULL em nos: do tenant): $ROLES" ;;
  esac
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
  if has_role knowledge && ! skipped knowledge && [ "$OS/$ARCH" = Darwin/arm64 ]; then
    KNOWLEDGE_NATIVE=1
  elif has_role knowledge && ! skipped knowledge; then
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

# "a >= b" para versões x.y.z (sem rebaixar o que a pessoa já tem mais novo que o fixado pelo kernel)
version_ge() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)" = "$1" ]; }

install_uv() {
  skipped uv && { log "uv: pulado"; return 0; }
  cur=$(uv --version 2>/dev/null | awk '{print $2}')
  if have uv && [ -n "$cur" ] && version_ge "$cur" "$UV_VERSION"; then
    log "uv $cur já presente (kernel pede $UV_VERSION)"; return 0
  fi
  log "instalando uv $UV_VERSION em ~/.local/bin"
  run "curl -LsSf 'https://astral.sh/uv/$UV_VERSION/install.sh' | env UV_INSTALL_DIR='$HOME/.local/bin' UV_NO_MODIFY_PATH=1 sh"
}

install_node() {
  skipped node && { log "node: pulado"; return 0; }
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    log "instalando nvm $NVM_VERSION em $NVM_DIR"
    mkdir -p "$JARVIS_HOME/logs"
    run "curl -fsSL 'https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh' | env PROFILE=/dev/null NVM_DIR='$NVM_DIR' bash >>'$JARVIS_HOME/logs/instalacao-node.log' 2>&1"
    [ "$DRY_RUN" = 1 ] || { mkdir -p "$JARVIS_HOME"; : > "$JARVIS_HOME/.nvm-instalado-pelo-kernel"; }
  fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] nvm install %s\n' "$NODE_VERSION"; return 0; fi
  mkdir -p "$JARVIS_HOME/logs"
  with_node "nvm install '$NODE_VERSION' >>'$JARVIS_HOME/logs/instalacao-node.log' 2>&1 && nvm alias default '$NODE_VERSION' >/dev/null 2>&1 && printf '[whitebeard-os] node %s pronto\n' \"\$(node -v)\""
}

install_claude() {
  has_role desktop || { log "claude: só no papel desktop"; return 0; }
  skipped claude && { log "claude: pulado"; return 0; }
  cur=$(claude --version 2>/dev/null | awk '{print $1; exit}')
  if have claude && [ -n "$cur" ] && version_ge "$cur" "$CLAUDE_CODE_VERSION"; then
    log "Claude Code $cur já presente (kernel pede $CLAUDE_CODE_VERSION)"; return 0
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


sha256_of() { if have sha256sum; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# Build próprio com o filtro whisper (macOS: Metal e avfoundation para câmera/microfone; Linux: CPU),
# publicado em whitebeard-os-releases, que é público: baixa por URL com sha256 conferido, sem credencial.
install_ffmpeg_whisper() {
  case "$OS/$ARCH" in
    Linux/x86_64) url="$FFMPEG_WHISPER_URL_LINUX_X86_64"; sha="$FFMPEG_WHISPER_SHA256_LINUX_X86_64" ;;
    Darwin/arm64) url="$FFMPEG_WHISPER_URL_DARWIN_ARM64"; sha="$FFMPEG_WHISPER_SHA256_DARWIN_ARM64" ;;
    *) return 1 ;;
  esac
  [ -n "$url" ] && [ -n "$sha" ] || return 1
  asset=$(basename "$url")
  dest="$JARVIS_HOME/tools/${asset%.tar.xz}"
  if [ -f "$dest/.ok" ] && [ -x "$dest/ffmpeg" ]; then log "ffmpeg com whisper já presente"; link_ffmpeg "$dest"; return 0; fi
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] baixar %s em %s\n' "$url" "$dest"; return 0; fi
  rm -rf "$dest"; mkdir -p "$dest"
  # sem o build próprio (release fora do ar, sha desatualizado), fica o build público, sem whisper
  if ! try_fetch_verified "$url" "$sha" "$dest/archive.tar.xz"; then rm -rf "$dest"; log "ffmpeg: build com whisper indisponível; usando o build público"; return 1; fi
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

# Claude conectado à conta da pessoa: Hermes e Paperclip usam esse login. Sem ele, os dois ficam sem
# modelo em silêncio; por isso o instalador confere e, num terminal, abre o login e espera.
ensure_claude_login() {
  skipped claude && return 0
  have claude || return 0
  [ "$DRY_RUN" = 1 ] && return 0
  if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then log "claude: conectado à sua conta"; return 0; fi
  if can_prompt; then
    log "claude: entre na sua conta Claude (vai abrir o navegador; volte aqui quando terminar)"
    claude auth login </dev/tty >/dev/tty 2>&1 || true
    if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then log "claude: conectado à sua conta"
    else log "aviso: Claude não conectado; Hermes e Paperclip ficam sem modelo até você rodar: claude auth login"; fi
  else
    log "aviso: Claude não conectado; rode num terminal: claude auth login"
  fi
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
    mkdir -p "$JARVIS_HOME/logs"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$HERMES_TAG" https://github.com/NousResearch/hermes-agent.git "$dir" >>"$JARVIS_HOME/logs/instalacao-hermes.log" 2>&1 \
      || die "hermes: clone do hermes-agent falhou"
  fi
  mkdir -p "$JARVIS_HOME/logs"
  log "hermes: instalando (alguns minutos; detalhes em $JARVIS_HOME/logs/instalacao-hermes.log)"
  (cd "$dir" && printf 'nn' | bash setup-hermes.sh) >>"$JARVIS_HOME/logs/instalacao-hermes.log" 2>&1 \
    || die "hermes: instalador upstream falhou (veja $JARVIS_HOME/logs/instalacao-hermes.log)"
  have hermes || [ -x "$HOME/.local/bin/hermes" ] || die "hermes: comando não encontrado depois da instalação"
}

# Voz do JARVIS Web: o app roda o daemon STT (faster-whisper) e o TTS (edge-tts) com o Python do venv do
# Hermes, sem passar pelo lazy-install do próprio Hermes, e o [all] upstream não traz mais esses extras.
# Instala os dois do uv.lock do checkout (hashes conferidos); --inexact preserva o que o Hermes já instalou
# sob demanda. Roda também com o Hermes já presente, para consertar instalações antigas.
ensure_hermes_voice() {
  has_role desktop || return 0
  { [ "$WITH_HERMES" = 1 ] && ! skipped hermes; } || return 0
  dir="$HERMES_HOME/hermes-agent"
  if [ "$DRY_RUN" = 1 ]; then log "hermes: garantiria os extras voice e edge-tts em $dir/venv"; return 0; fi
  if [ ! -x "$dir/venv/bin/python3" ] || [ ! -f "$dir/uv.lock" ]; then
    log "aviso: venv ou uv.lock do Hermes ausente em $dir; voz do JARVIS Web indisponível"; return 0
  fi
  "$dir/venv/bin/python3" -c 'import faster_whisper, edge_tts' 2>/dev/null && return 0
  log "hermes: instalando a voz do JARVIS Web (faster-whisper, edge-tts) no venv do Hermes"
  # mesmo isolamento do setup-hermes.sh upstream: o lock vale com o pyproject do checkout, sem config do usuário
  cfg=$(mktemp -d)
  (cd "$dir" && unset UV_NO_CONFIG UV_CONFIG_FILE && XDG_CONFIG_HOME="$cfg" XDG_CONFIG_DIRS="$cfg" \
    UV_PROJECT_ENVIRONMENT="$dir/venv" uv sync --locked --inexact --extra voice --extra edge-tts) >>"$JARVIS_HOME/logs/instalacao-hermes.log" 2>&1 \
    || log "aviso: não consegui instalar a voz no venv do Hermes; o JARVIS Web segue sem STT"
  rmdir "$cfg" 2>/dev/null || true
}

install_paperclip() {
  has_role core || return 0
  skipped paperclip && { log "paperclip: pulado"; return 0; }
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] npm install -g paperclipai@%s\n' "$PAPERCLIP_VERSION"; return 0; fi
  # atalho da "loja gerenciada" do Paperclip (paperclipai install) que ficou sem alvo depois de uma
  # desinstalação: vem antes do npm no PATH e quebra tudo com "Cannot find module .../cli/current/..."
  shim="$HOME/.local/bin/paperclipai"
  if [ -f "$shim" ] && grep -q "managed install shim" "$shim" 2>/dev/null && [ ! -e "$HOME/.paperclip/cli/current" ]; then
    rm -f "$shim"; log "paperclip: atalho órfão de instalação antiga removido ($shim)"
  fi
  if with_node "npm ls -g 'paperclipai@$PAPERCLIP_VERSION' >/dev/null 2>&1"; then
    log "paperclip $PAPERCLIP_VERSION já presente"
  else
    log "instalando paperclip $PAPERCLIP_VERSION"
    with_node "npm install -g --no-fund --no-audit 'paperclipai@$PAPERCLIP_VERSION' >/dev/null"
  fi
  # `paperclipai service install` escreve a unidade apontando para ~/.local/bin/paperclipai (o atalho da loja
  # gerenciada, que o npm não cria): sem ele o serviço morre com "no executable" e o doctor recusa subir.
  # Atalho do kernel para o binário do npm, só quando não há atalho da pessoa (loja gerenciada) no lugar.
  bin=$(with_node 'command -v paperclipai' | tail -n 1)
  if [ -n "$bin" ] && { [ ! -e "$shim" ] || grep -q "whitebeard-os: atalho" "$shim" 2>/dev/null; }; then
    mkdir -p "$HOME/.local/bin"
    printf '#!/bin/sh\n# whitebeard-os: atalho para o paperclipai instalado pelo npm (nvm); refeito a cada bootstrap\nexec %s "$@"\n' "$bin" >"$shim"
    chmod 755 "$shim"
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
  asked=1; ask_secrets || asked=0
  telegram_setup && asked=1 && TELEGRAM_CHANGED=1
  if [ "$asked" = 1 ]; then
    uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLES" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR" >/dev/null
  fi
  return 0
}

# Segredos obrigatórios ainda vazios: pergunta no terminal (pelo /dev/tty, que funciona mesmo com
# `curl | sh`), grava em secrets.env e devolve 0 para o render rodar de novo. Sem terminal, só avisa.
# Grava NOME=valor em secrets.env (entre aspas simples), substituindo a linha se ela existir.
set_secret() {
  python3 - "$JARVIS_HOME/secrets.env" "$1" "$2" <<'PY'
import re, sys
p, name, value = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
q = "'" + value.replace("'", "'\\''") + "'"
s2, n = re.subn(r"(?m)^" + re.escape(name) + r"=.*$", lambda _: name + "=" + q, s)
open(p, "w", encoding="utf-8").write(s2 if n else s + "\n" + name + "=" + q + "\n")
PY
}

# Terminal de verdade para perguntar (ssh sem -t, cron e CI têm /dev/tty que não abre).
can_prompt() { [ "$NO_PROMPT" != 1 ] && ( : </dev/tty ) 2>/dev/null; }

# Telegram é opcional. Com --telegram (ou com o token já preenchido e o chat ainda vazio), num terminal: pede
# o token sem eco e reconhece o chat do dono pela primeira mensagem ao bot. O chat vira TELEGRAM_HOME_CHANNEL
# (e, no render, a allowlist do gateway). Sem curl, sem descobrir id à mão, sem pareamento.
telegram_setup() {
  has_role core || return 1
  s="$JARVIS_HOME/secrets.env"
  if [ "$WITH_TELEGRAM" != 1 ] && ! grep -q '^TELEGRAM_BOT_TOKEN=.' "$s" 2>/dev/null; then
    # sem token e sem --telegram: pergunta uma vez (a resposta fica guardada); "não" nunca pergunta de novo
    [ -f "$JARVIS_HOME/telegram.decisao" ] && return 1
    can_prompt || return 1
    printf '\n  Quer falar com o JARVIS pelo Telegram? Pode ligar depois com ./install.sh --telegram. [s/N] ' >/dev/tty
    IFS= read -r resp </dev/tty
    case "$resp" in s|S|sim|Sim|SIM|y|Y) WITH_TELEGRAM=1 ;; *) printf 'nao\n' >"$JARVIS_HOME/telegram.decisao"; log "telegram: desligado (para ligar: ./install.sh --telegram)"; return 1 ;; esac
  fi
  if [ "$WITH_TELEGRAM" != 1 ]; then
    grep -q '^TELEGRAM_HOME_CHANNEL=.' "$s" 2>/dev/null && return 1
  fi
  if ! can_prompt; then
    log "telegram: para ligar, rode o instalador num terminal com --telegram"
    return 1
  fi
  if ! grep -q '^TELEGRAM_BOT_TOKEN=.' "$s" 2>/dev/null; then
    printf '\n  Telegram: no app, abra @BotFather, mande /newbot, escolha um nome e copie o token.\n  Token do bot (Enter em branco para deixar para depois): ' >/dev/tty
    stty -echo </dev/tty 2>/dev/null || true
    IFS= read -r value </dev/tty
    stty echo </dev/tty 2>/dev/null || true
    printf '\n' >/dev/tty
    [ -n "$value" ] || return 1
    set_secret TELEGRAM_BOT_TOKEN "$value"
  fi
  grep -q '^TELEGRAM_HOME_CHANNEL=.' "$s" 2>/dev/null && return 0
  # o gateway, se já roda com este token, disputaria as mensagens: para e volta em install_services
  if have hermes; then hermes gateway stop >/dev/null 2>&1 || true; fi
  chat=$(python3 - "$s" <<'PY'
import json, shlex, sys, time, urllib.error, urllib.request
tok = ""
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("TELEGRAM_BOT_TOKEN="):
        tok = (shlex.split(line.split("=", 1)[1]) or [""])[0]
def api(method, **q):
    url = f"https://api.telegram.org/bot{tok}/{method}?" + "&".join(f"{k}={v}" for k, v in q.items())
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)["result"]
try:
    me = api("getMe")
except Exception:
    print("[whitebeard-os] aviso: o Telegram recusou o token (confira em @BotFather)", file=sys.stderr); sys.exit(0)
print(f"\n  Mande \"oi\" para @{me['username']} no Telegram. Espero até 3 minutos...", file=open("/dev/tty", "w"))
# o backlog do bot pode ter mensagem de qualquer pessoa: descarta tudo o que chegou antes deste momento
started = int(time.time())
offset = 0
try:
    last = api("getUpdates", offset=-1, timeout=0)
    if last:
        offset = last[-1]["update_id"] + 1
except Exception:
    pass
deadline = time.time() + 180
while time.time() < deadline:
    try:
        ups = api("getUpdates", timeout=30, offset=offset, allowed_updates='["message"]')
    except urllib.error.HTTPError as e:
        print(f"[whitebeard-os] aviso: o Telegram não entregou mensagens (HTTP {e.code})", file=sys.stderr); sys.exit(0)
    for u in ups:
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        if m.get("date", 0) < started:
            continue
        if (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
            api("getUpdates", offset=offset, timeout=0)  # marca como lida: o gateway não responde a ela de novo
            print(m["from"]["id"]); sys.exit(0)
PY
)
  if [ -n "$chat" ]; then
    set_secret TELEGRAM_HOME_CHANNEL "$chat"
    log "telegram: seu chat foi reconhecido; o bot já responde só a você"
  else
    log "aviso: nenhuma mensagem chegou ao bot; rode de novo com --telegram quando quiser"
  fi
  return 0
}

ask_secrets() {
  f="$JARVIS_HOME/render/secrets-missing.txt"
  [ -s "$f" ] || return 1
  # sem terminal de verdade (ssh sem -t, cron, CI) o /dev/tty existe mas não abre: só avisa
  if ! can_prompt; then
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
    set_secret "$name" "$value"
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
      if ! grep -q "^$k=" "$HERMES_HOME/.env"; then
        printf '%s\n' "$line" >> "$HERMES_HOME/.env"
      else
        # chaves que o kernel gera ou deriva mudam de valor (token do cérebro recriado, chat do dono):
        # essas são atualizadas; as demais (segredos da pessoa) nunca são sobrescritas
        case "$k" in GBRAIN_MCP_TOKEN|TELEGRAM_ALLOWED_USERS|VAULT_PATH|JARVIS_TENANT)
          grep -qxF "$line" "$HERMES_HOME/.env" || python3 - "$HERMES_HOME/.env" "$k" "$line" <<'PY'
import re, sys
p, k, line = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(re.sub(r"(?m)^" + re.escape(k) + r"=.*$", lambda _: line, s, count=1))
PY
        ;; esac
      fi
    done < "$JARVIS_HOME/render/hermes/env"
    log "hermes: variáveis do tenant mescladas em $HERMES_HOME/.env (sem sobrescrever)"
  fi
}

# Plugins do Claude Code que o tenant declara em `claude:` (marketplaces e plugins), só no papel desktop.
apply_claude_plugins() {
  has_role desktop || return 0
  skipped plugins && { log "plugins: pulados"; return 0; }
  script="$JARVIS_HOME/render/claude-plugins.sh"
  if [ "$DRY_RUN" = 1 ]; then log "plugins: aplicaria $script, se o tenant declarar claude.plugins"; return 0; fi
  [ -f "$script" ] || return 0
  have claude || { log "plugins: claude ausente; pulei"; return 0; }
  sh "$script"
}

# Configuração do tenant que precisa entrar num config.yaml do Hermes já existente (apply_hermes_config só
# escreve o inicial): MCPs do tenant e reparo de um fallback_model inválido (string, de versões antigas do
# kernel). `hermes mcp add` é interativo (sonda a conexão e pergunta), então a mescla usa load_config/
# save_config e a validação do próprio Hermes: só as entradas do tenant são escritas, por cima dos mesmos
# nomes (filtros de tools escolhidos à mão ficam); servidores adicionados à mão não são tocados.
apply_hermes_tenant() {
  [ "$WITH_HERMES" = 1 ] || return 0
  skipped mcp && return 0
  src="$JARVIS_HOME/render/hermes"
  [ -f "$src/mcp.json" ] || return 0
  if [ "$DRY_RUN" = 1 ]; then log "hermes: mesclaria os MCPs do tenant no config.yaml"; return 0; fi
  py="$HERMES_HOME/hermes-agent/venv/bin/python3"
  [ -x "$py" ] || { log "aviso: venv do Hermes ausente; configuração do tenant não aplicada no Hermes"; return 0; }
  HERMES_HOME="$HERMES_HOME" "$py" - "$src" <<'PY' || log "aviso: não consegui aplicar a configuração do tenant no Hermes"
import json, os, sys
import yaml
from hermes_cli.config import load_config, save_config
from hermes_cli.mcp_security import validate_mcp_server_entry

src = sys.argv[1]
wanted = json.load(open(os.path.join(src, "mcp.json"), encoding="utf-8"))
rendered = {}
if os.path.exists(os.path.join(src, "config.yaml")):
    rendered = yaml.safe_load(open(os.path.join(src, "config.yaml"), encoding="utf-8")) or {}
config = load_config()
changed = False
servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
applied = []
managed_file = os.path.join(src, "mcp.applied")
managed = open(managed_file, encoding="utf-8").read().split() if os.path.exists(managed_file) else []
for stale in [n for n in managed if n not in wanted and n in servers]:
    del servers[stale]; changed = True
    print(f"[whitebeard-os] hermes: mcp {stale} removido (saiu do tenant)")
for name, entry in wanted.items():
    issues = validate_mcp_server_entry(name, entry)
    if issues:
        print(f"[whitebeard-os] aviso: hermes recusou o mcp {name}: {'; '.join(issues)}")
        continue
    merged = {**(servers.get(name) or {}), **entry}
    if servers.get(name) != merged:
        servers[name] = merged
        changed = True
    applied.append(name)
if changed:
    config["mcp_servers"] = servers
fb = rendered.get("fallback_model")
if isinstance(config.get("fallback_model"), str) and isinstance(fb, dict):
    config["fallback_model"] = fb
    changed = True
    print("[whitebeard-os] hermes: fallback_model reparado (string → {provider, model})")
if changed:
    save_config(config)
open(managed_file, "w", encoding="utf-8").write("\n".join(applied) + "\n")
print(f"[whitebeard-os] hermes: mcp ({len(applied)}): {', '.join(applied) or '-'}")
PY
}

# Second brain da pessoa: a pasta VAULT_PATH do tenant (padrão ~/whitebeard-secondbrain). Com vault.repo e
# acesso, clona; senão cria um vault local. Sempre um repositório git com a estrutura que as skills de
# documentação esperam (tenants/<tenant>/{projects-doc,knowledge-domain,resources}); abre no Obsidian.
# Commits são feitos pelo kernel com identidade própria: a pessoa não precisa saber git.
ensure_vault() {
  skipped render && return 0
  [ "$DRY_RUN" = 1 ] && return 0
  vault=$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env")
  tenant=$(sed -n 's/^JARVIS_TENANT=//p' "$JARVIS_HOME/profile.env")
  [ -n "$vault" ] && [ -n "$tenant" ] || return 0
  repo=$(python3 - "$TENANT" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^vault:\s*\n((?:[ \t].*\n?)+)", txt, re.M)
r = re.search(r"^\s*repo:\s*([^\s#]+)", m.group(1), re.M) if m else None
print(r.group(1) if r else "")
PY
)
  if [ ! -d "$vault" ] && [ -n "$repo" ]; then
    log "vault: clonando $repo em $vault"
    git clone -q "$repo" "$vault" 2>/dev/null || log "aviso: não consegui clonar $repo (sem acesso?); criando um second brain local"
  fi
  [ -d "$vault" ] || { log "vault: criando o second brain em $vault"; mkdir -p "$vault"; }
  td="$vault/tenants/$tenant"
  seeded=0
  if [ -d "$td" ]; then
    # vault que já existia, com a pasta do tenant: pastas e índices são da pessoa; nada é semeado
    log "vault: $vault já tem tenants/$tenant; nada a semear"
  else
  seeded=1
  mkdir -p "$td/projects-doc" "$td/knowledge-domain" "$td/resources"
  if [ ! -f "$vault/README.md" ]; then
    cat >"$vault/README.md" <<EOF
---
title: Second brain
---

# Second brain

Este é o seu cérebro de conhecimento. Tudo o que você e os seus agentes documentam fica aqui, em arquivos
Markdown que você pode abrir no Obsidian (abra esta pasta como cofre) ou em qualquer editor.

O Whitebeard OS indexa esta pasta sozinho: o que você escrever aqui passa a ser pesquisável pelos agentes em
poucos minutos. O histórico é guardado automaticamente; você não precisa de git.

## Onde fica cada coisa

- [[tenants/$tenant/projects-doc/README|projects-doc]]: como os seus projetos e sistemas funcionam.
- [[tenants/$tenant/knowledge-domain/README|knowledge-domain]]: conhecimento do seu negócio e da sua área,
  que continua valendo mesmo sem nenhum projeto.
- [[tenants/$tenant/resources/README|resources]]: material bruto, capturas e rascunhos ainda não organizados.
EOF
  fi
  [ -f "$td/README.md" ] || cat >"$td/README.md" <<EOF
---
title: Índice de $tenant
type: index
---

# $tenant

- [[projects-doc/README|Documentação de projetos]]
- [[knowledge-domain/README|Conhecimento de domínio]]
- [[resources/README|Recursos brutos]]
EOF
  [ -f "$td/projects-doc/README.md" ] || cat >"$td/projects-doc/README.md" <<EOF
---
title: Documentação de projetos de $tenant
type: index
---

# Documentação de projetos

Uma pasta por projeto ou sistema (arquitetura, configuração, operação, runbooks, decisões). Este índice
lista os projetos documentados; mantenha-o atualizado ao criar um novo.
EOF
  [ -f "$td/knowledge-domain/README.md" ] || cat >"$td/knowledge-domain/README.md" <<EOF
---
title: Conhecimento de domínio de $tenant
type: index
---

# Conhecimento de domínio

Conceitos, regras, glossário e processos do seu negócio ou da sua área: o que continuaria útil mesmo que
nenhum sistema existisse.
EOF
  [ -f "$td/resources/README.md" ] || cat >"$td/resources/README.md" <<EOF
---
title: Recursos brutos de $tenant
---

# Recursos

Pouso temporário: capturas, transcrições, rascunhos e material misto que ainda vai ser organizado em
projects-doc ou knowledge-domain.
EOF
  fi
  [ -f "$vault/.gitignore" ] || { printf '.DS_Store\n.trash/\n.obsidian/workspace*.json\n.obsidian/cache\n' >"$vault/.gitignore"; seeded=1; }
  # o GBrain nativo desta máquina grava o marcador de dono dentro do vault: não é conhecimento, não vai para o git
  if has_role knowledge && ! grep -q '^\.gbrain-owner\.json$' "$vault/.gitignore" 2>/dev/null; then
    printf '# marcador interno do GBrain (dono do PGLite); não é conhecimento\n.gbrain-owner.json\n' >>"$vault/.gitignore"; seeded=1
  fi
  [ -d "$vault/.git" ] || git -C "$vault" init -q
  # só o que o kernel escreveu entra no commit; o trabalho da pessoa fica como está
  if [ "$seeded" = 1 ]; then
    git -C "$vault" add -- .gitignore README.md "tenants/$tenant" >/dev/null 2>&1 || true
    git -C "$vault" diff --cached --quiet 2>/dev/null \
      || git -C "$vault" -c user.name="Whitebeard OS" -c user.email="whitebeard-os@localhost" commit -q -m "whitebeard-os: estrutura do second brain"
  fi
  log "vault: second brain em $vault (abra no Obsidian)"
}

# Baixa url em dest conferindo o sha256. try_fetch_verified devolve 1 em falha (para quem tem fallback);
# fetch_verified morre: binário ausente ou adulterado não roda.
try_fetch_verified() {
  curl -fsSL "$1" -o "$3.part" || { rm -f "$3.part"; log "download falhou: $1"; return 1; }
  got=$(sha256_of "$3.part")
  [ "$got" = "$2" ] || { rm -f "$3.part"; log "sha256 não confere para $1 (esperado $2, veio $got)"; return 1; }
  mv "$3.part" "$3"
}
fetch_verified() { try_fetch_verified "$@" || die "não consegui baixar com segurança: $1"; }

# Serviço de usuário do macOS (LaunchAgent): rótulo, log e o comando; variáveis de ambiente em KEY=VALUE
# depois de "--". Recria o plist e religa, esperando o bootout terminar (é assíncrono).
launch_agent() {
  label=$1; logf=$2; shift 2
  interval=""
  if [ "${1:-}" = --interval ]; then interval=$2; shift 2; fi
  args=""; envs=""
  while [ $# -gt 0 ] && [ "$1" != -- ]; do args="$args<string>$1</string>"; shift; done
  [ "${1:-}" = -- ] && shift
  for kv in "$@"; do envs="$envs<key>${kv%%=*}</key><string>${kv#*=}</string>"; done
  plist="$HOME/Library/LaunchAgents/$label.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$logf")"
  keep="<key>KeepAlive</key><true/>"
  [ -n "$interval" ] && keep="<key>StartInterval</key><integer>$interval</integer>"
  cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>EnvironmentVariables</key><dict>$envs</dict>
  <key>RunAtLoad</key><true/>
  $keep
  <key>StandardOutPath</key><string>$logf</string>
  <key>StandardErrorPath</key><string>$logf</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  i=0
  while launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && [ "$i" -lt 20 ]; do i=$((i + 1)); sleep 0.5; done
  launchctl bootstrap "gui/$(id -u)" "$plist"
}

wait_http() { i=0; until curl -fs -o /dev/null "$1" 2>/dev/null; do i=$((i + 1)); [ "$i" -gt "$2" ] && return 1; sleep 1; done; }

# Knowledge nativo (macOS, tudo-em-um): GBrain com PGLite (o motor padrão do GBrain para cérebro pessoal,
# Postgres 17 embutido) no Bun, e Ollama nativo com GPU Metal para os embeddings. Tudo em $JARVIS_HOME/tools
# e serviços de usuário; sem Docker, sem sudo. Docker/pgvector fica para cérebro compartilhado ou grande.
install_knowledge_native() {
  tools="$JARVIS_HOME/tools"
  bun_dir="$tools/bun-$BUN_VERSION"; ollama_dir="$tools/ollama-$OLLAMA_VERSION"; gb_dir="$tools/gbrain-$GBRAIN_REF"
  if [ "$DRY_RUN" = 1 ]; then log "knowledge: instalaria Bun $BUN_VERSION, Ollama $OLLAMA_VERSION e GBrain $GBRAIN_REF (PGLite) nativos"; return 0; fi
  mkdir -p "$tools"
  if [ ! -x "$bun_dir/bun" ]; then
    log "knowledge: baixando Bun $BUN_VERSION"
    tmp=$(mktemp -d)
    fetch_verified "$BUN_URL_DARWIN_ARM64" "$BUN_SHA256_DARWIN_ARM64" "$tmp/bun.zip"
    (cd "$tmp" && unzip -q bun.zip) && mkdir -p "$bun_dir" && mv "$(find "$tmp" -type f -name bun | head -n 1)" "$bun_dir/bun"
    chmod +x "$bun_dir/bun"; rm -rf "$tmp"
  fi
  if [ ! -x "$ollama_dir/ollama" ]; then
    log "knowledge: baixando Ollama $OLLAMA_VERSION (embeddings na GPU)"
    tmp=$(mktemp -d)
    fetch_verified "$OLLAMA_URL_DARWIN_ARM64" "$OLLAMA_SHA256_DARWIN_ARM64" "$tmp/ollama.tgz"
    mkdir -p "$ollama_dir" && tar -xzf "$tmp/ollama.tgz" -C "$ollama_dir"; rm -rf "$tmp"
  fi
  if [ ! -f "$gb_dir/.ok" ]; then
    log "knowledge: instalando GBrain $GBRAIN_REF"
    rm -rf "$gb_dir"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$GBRAIN_REF" https://github.com/garrytan/gbrain.git "$gb_dir" >>"$JARVIS_HOME/logs/instalacao-gbrain.log" 2>&1 \
      || die "knowledge: clone do GBrain falhou"
    # os scripts do GBrain (postinstall) chamam `bun` pelo PATH
    (cd "$gb_dir" && PATH="$bun_dir:$PATH" bun install --frozen-lockfile --silent) || die "knowledge: bun install do GBrain falhou"
    : > "$gb_dir/.ok"
  fi
  mkdir -p "$HOME/.local/bin"
  printf '#!/bin/sh\n# gerado pelo whitebeard-os: GBrain %s no Bun %s\nexec "%s" "%s/src/cli.ts" "$@"\n' \
    "$GBRAIN_REF" "$BUN_VERSION" "$bun_dir/bun" "$gb_dir" > "$HOME/.local/bin/gbrain"
  chmod +x "$HOME/.local/bin/gbrain"
  # instalação anterior em Docker ocupa a porta 3131: desliga (os volumes ficam, nada é apagado)
  if have docker && docker compose --project-directory "$JARVIS_HOME/compose/knowledge" ps -q 2>/dev/null | grep -q .; then
    log "knowledge: desligando a versão em Docker (volumes preservados)"
    docker compose --project-directory "$JARVIS_HOME/compose/knowledge" down >/dev/null 2>&1 || true
  fi
  [ "$NO_START" = 1 ] && { log "knowledge: --no-start; serviços nativos não iniciados"; return 0; }
  launch_agent dev.whitebeard.ollama "$JARVIS_HOME/logs/ollama.log" "$ollama_dir/ollama" serve -- \
    "OLLAMA_HOST=127.0.0.1:11434" "HOME=$HOME"
  wait_http http://127.0.0.1:11434/api/version 30 || die "knowledge: o Ollama não respondeu em 127.0.0.1:11434"
  if ! OLLAMA_HOST=127.0.0.1:11434 "$ollama_dir/ollama" list 2>/dev/null | grep -q "^$EMBED_MODEL"; then
    log "knowledge: baixando o modelo de embeddings $EMBED_MODEL (cerca de 1,2 GB, uma vez só)"
    OLLAMA_HOST=127.0.0.1:11434 "$ollama_dir/ollama" pull "$EMBED_MODEL" >/dev/null || die "knowledge: falha ao baixar $EMBED_MODEL"
  fi
  gb_env="OLLAMA_BASE_URL=http://127.0.0.1:11434/v1"
  export PATH="$bun_dir:$PATH"
  # o PGLite tem um dono só: com o serve no ar, init e migrações não abrem o banco
  launchctl bootout "gui/$(id -u)/dev.whitebeard.gbrain" 2>/dev/null || true
  i=0; while launchctl print "gui/$(id -u)/dev.whitebeard.gbrain" >/dev/null 2>&1 && [ "$i" -lt 20 ]; do i=$((i + 1)); sleep 0.5; done
  if [ ! -f "$HOME/.gbrain/config.json" ]; then
    # Cérebro novo: uma reserva de identidade do second brain deixada por um cérebro anterior (fica no
    # diretório pai do vault, como .gbrain-owner-<sha256 do caminho>.json) faria o GBrain recusar a pasta
    # ("recovery_required: belongs to another owner"). Só aqui, na criação, ela é sabidamente órfã.
    vault=$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env")
    [ -n "$vault" ] && python3 - "$vault" <<'PY'
import glob, json, os, sys
vault = os.path.realpath(sys.argv[1])
for f in glob.glob(os.path.join(os.path.dirname(vault), ".gbrain-owner-*.json")):
    try:
        if os.path.realpath(json.load(open(f)).get("root", "")) == vault:
            os.remove(f); print(f"[whitebeard-os] knowledge: reserva órfã de um cérebro anterior removida ({os.path.basename(f)[:30]}…)")
    except Exception:
        pass
PY
    env "$gb_env" "$HOME/.local/bin/gbrain" init --pglite --non-interactive \
      --embedding-model "ollama:$EMBED_MODEL" --embedding-dimensions 1024 >>"$JARVIS_HOME/logs/instalacao-gbrain.log" 2>&1 \
      || die "knowledge: gbrain init falhou (veja $JARVIS_HOME/logs/instalacao-gbrain.log)"
    log "knowledge: cérebro criado (PGLite em ~/.gbrain)"
  fi
  # a versão do GBrain é fixada pelo kernel: o aviso de self-upgrade em cada comando só confundiria
  python3 - "$HOME/.gbrain/config.json" <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p, encoding="utf-8"))
su = c.setdefault("self_upgrade", {})
if su.get("mode") != "off":
    su.update({"mode": "off", "mode_prompted": True}); json.dump(c, open(p, "w", encoding="utf-8"), indent=2)
PY
  env "$gb_env" "$HOME/.local/bin/gbrain" apply-migrations --yes --no-autopilot-install >>"$JARVIS_HOME/logs/instalacao-gbrain.log" 2>&1 \
    || die "knowledge: migrações do GBrain falharam (veja $JARVIS_HOME/logs/instalacao-gbrain.log)"
  # token do MCP (uma vez; só com o serve parado o CLI abre o banco). Nunca vai para o log.
  tok=$(sed -n "s/^GBRAIN_MCP_TOKEN='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$JARVIS_HOME/secrets.env")
  if [ -z "$tok" ] || ! env "$gb_env" "$HOME/.local/bin/gbrain" auth list 2>/dev/null | grep whitebeard-os | grep -qv REVOKED; then
    tok=$(env "$gb_env" "$HOME/.local/bin/gbrain" auth create whitebeard-os --scopes read,write 2>/dev/null | grep -oE 'gbrain_[A-Za-z0-9]{40,}' | head -n 1)
    [ -n "$tok" ] || die "knowledge: não consegui criar o token do MCP do GBrain"
    set_secret GBRAIN_MCP_TOKEN "$tok"
    RERENDER=1
    log "knowledge: token do MCP criado (guardado em secrets.env)"
  fi
  unset tok
  # o second brain é a fonte do cérebro; primeira sincronização direta (serve parado)
  vault=$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env")
  if [ -d "$vault/.git" ]; then
    if ! env "$gb_env" "$HOME/.local/bin/gbrain" sources list 2>/dev/null | grep -q secondbrain; then
      if env "$gb_env" "$HOME/.local/bin/gbrain" sources add secondbrain --path "$vault" --name "Second brain" >/dev/null 2>&1; then
        log "knowledge: second brain registrado como fonte do cérebro"
      else
        log "aviso: não consegui registrar $vault como fonte do GBrain"
      fi
    fi
    # o token só vê as fontes do seu grant: second brain primeiro (fonte de escrita) e a pasta default
    env "$gb_env" "$HOME/.local/bin/gbrain" auth rescope --token whitebeard-os --sources secondbrain,default >/dev/null 2>&1 \
      || log "aviso: não consegui dar ao token do MCP acesso ao second brain"
    env "$gb_env" "$HOME/.local/bin/gbrain" sync --no-pull --working-tree >/dev/null 2>&1 || log "aviso: primeira sincronização do second brain falhou"
  fi
  launch_agent dev.whitebeard.gbrain "$JARVIS_HOME/logs/gbrain.log" "$HOME/.local/bin/gbrain" serve --http \
    --port 3131 --bind 127.0.0.1 --public-url http://localhost:3131 -- \
    "$gb_env" "EMBED_MODEL=$EMBED_MODEL" "GBRAIN_AI_EMBED_TIMEOUT_MS=300000" "HOME=$HOME" \
    "PATH=$bun_dir:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  wait_http http://127.0.0.1:3131/health 60 || log "aviso: o GBrain ainda não respondeu em 127.0.0.1:3131 (veja $JARVIS_HOME/logs/gbrain.log)"
  # a cada 2 min: guarda o que mudou no second brain (commit local) e sincroniza com o cérebro pelo serve
  if [ -d "$vault/.git" ]; then
    mkdir -p "$JARVIS_HOME/bin"
    cat >"$JARVIS_HOME/bin/vault-sync.sh" <<EOF
#!/bin/sh
# gerado pelo whitebeard-os: commita o que mudou no second brain e sincroniza com o GBrain (serve no ar)
export PATH="$bun_dir:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOME"
export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
cd "$vault" || exit 0
git add -A >/dev/null 2>&1
git diff --cached --quiet || git -c user.name="Whitebeard OS" -c user.email="whitebeard-os@localhost" commit -q -m "auto: \$(date '+%Y-%m-%d %H:%M')"
curl -fs -o /dev/null http://127.0.0.1:3131/health || exit 0
exec gbrain sync --no-pull --working-tree
EOF
    chmod +x "$JARVIS_HOME/bin/vault-sync.sh"
    launch_agent dev.whitebeard.vault-sync "$JARVIS_HOME/logs/vault-sync.log" --interval 120 "$JARVIS_HOME/bin/vault-sync.sh"
  fi
  log "knowledge: GBrain em http://localhost:3131 (MCP em /mcp), PGLite em ~/.gbrain, embeddings $EMBED_MODEL na GPU"
}

# O token do MCP nasce dentro de install_knowledge, depois do render: com ele criado, o render e os MCPs
# (Claude Code e Hermes) são reaplicados nesta mesma execução, e o gateway é reiniciado para ver o cérebro.
apply_knowledge_render() {
  [ "$RERENDER" = 1 ] || return 0
  uv run --quiet "$KERNEL_DIR/bin/jarvis_render.py" --tenant "$TENANT" --role "$ROLES" --home "$JARVIS_HOME" --kernel "$KERNEL_DIR" >/dev/null
  apply_mcp
  apply_hermes_config   # o .env do Hermes ganha GBRAIN_MCP_TOKEN (mescla sem sobrescrever)
  apply_hermes_tenant
  HERMES_RESTART=1
}

install_knowledge() {
  has_role knowledge || return 0
  skipped knowledge && { log "knowledge: pulado"; return 0; }
  if [ "$KNOWLEDGE_NATIVE" = 1 ]; then install_knowledge_native; return 0; fi
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

# Paperclip só precisa da rede quando outro host do tenant fala com ele (desktop ou satélite em outra
# máquina). No tudo-em-um ele fica no loopback em modo local_trusted: sem convite, login nem chave de API, e
# a conexão Claude usa a conta desta máquina (no modo autenticado ela exige um login isolado que, no macOS,
# o Paperclip não consegue ler: o Claude Code guarda o token no Keychain).
paperclip_bind() {
  others=$(nos_query others)
  if has_role desktop && [ "$others" = 0 ]; then echo loopback; else echo lan; fi
}

# Instância existente em modo autenticado num tudo-em-um: passa para loopback local_trusted com os mesmos
# campos que o preset loopback do `paperclipai onboard` grava. Os dados (empresas, agentes) ficam no banco.
# Só com --paperclip-local: uma instância em produção pode ter clientes na rede (outro computador, celular)
# que deixariam de alcançá-la.
paperclip_to_loopback() {
  python3 - "$1" <<'PY'
import json, sys
path = sys.argv[1]
cfg = json.load(open(path, encoding="utf-8"))
srv = cfg.setdefault("server", {})
if srv.get("deploymentMode") == "local_trusted" and srv.get("bind") == "loopback":
    sys.exit(1)
srv.update({"deploymentMode": "local_trusted", "exposure": "private", "bind": "loopback", "host": "127.0.0.1"})
srv.pop("customBindHost", None)
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
PY
}

# Paperclip pronto para usar, sem wizard (só no modo local, em que a máquina é o operador): empresa com o
# nome do tenant, conexão "Claude desta máquina" (a conta do Claude Code já logada, compartilhada com todos os
# agentes) e um primeiro agente CEO com heartbeat desligado (só trabalha quando recebe tarefa). Idempotente:
# cada item só é criado se não existir; empresa e agentes criados à mão não são tocados.
paperclip_setup() {
  python3 - "$TENANT" <<'PY' || log "aviso: não consegui preparar o Paperclip (empresa, conexão Claude, agente)"
import json, re, sys, time, urllib.error, urllib.request

API = "http://127.0.0.1:3100/api"
tenant = re.search(r"^tenant:\s*([^\s#]+)", open(sys.argv[1], encoding="utf-8").read(), re.M).group(1)

def call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")

def items(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])

for _ in range(30):  # o serviço acabou de (re)iniciar
    try:
        call("GET", "/health", timeout=3); break
    except Exception:
        time.sleep(2)
companies = items(call("GET", "/companies"), "companies")
company = next((c for c in companies if c.get("name", "").lower() == tenant.lower()), None) or (companies[0] if companies else None)
if company is None:
    company = call("POST", "/companies", {"name": tenant})
    print(f"[whitebeard-os] paperclip: empresa '{tenant}' criada")
cid = company["id"]
conns = items(call("GET", f"/companies/{cid}/ai-connections"), "connections")
if not any(c.get("provider") == "anthropic" and c.get("status") == "connected" for c in conns):
    try:
        call("POST", f"/companies/{cid}/ai-connections/local", {"provider": "anthropic", "method": "subscription",
             "name": "Claude desta máquina", "ownership": "shared", "allAgents": True}, timeout=90)
        print("[whitebeard-os] paperclip: conexão 'Claude desta máquina' criada (assinatura do Claude Code)")
    except urllib.error.HTTPError as e:
        print(f"[whitebeard-os] aviso: paperclip sem conexão Claude (HTTP {e.code}): entre no Claude Code com `claude` e rode de novo")
if not items(call("GET", f"/companies/{cid}/agents"), "agents"):
    call("POST", f"/companies/{cid}/agents", {"name": "CEO", "role": "ceo", "adapterType": "claude_local",
         "onboardingFirstAgent": True})
    print("[whitebeard-os] paperclip: primeiro agente (CEO) criado, heartbeat desligado")
print(f"[whitebeard-os] paperclip: pronto (empresa '{company['name']}')")
PY
}

install_services() {
  has_role core || return 0
  skipped services && { log "serviços: pulados"; return 0; }
  bind=$(paperclip_bind)
  onboard="paperclipai onboard -y --install-service"
  [ "$bind" = lan ] && onboard="paperclipai onboard -y --bind lan --install-service"
  if [ "$DRY_RUN" = 1 ] || [ "$NO_START" = 1 ]; then
    log "serviços: não instalados (--no-start). Depois: $onboard; hermes gateway install --start-on-login"
    return 0
  fi
  pc_config="$HOME/.paperclip/instances/default/config.json"
  if [ ! -f "$pc_config" ]; then
    log "paperclip: primeira configuração ($( [ "$bind" = loopback ] && echo 'só esta máquina, sem login' || echo 'rede local, autenticado' )) e serviço de usuário"
    mkdir -p "$JARVIS_HOME/logs"
    with_node "$onboard </dev/null >>'$JARVIS_HOME/logs/instalacao-paperclip.log' 2>&1" \
      || log "aviso: a primeira configuração do Paperclip falhou (veja $JARVIS_HOME/logs/instalacao-paperclip.log)"
  else
    if [ "$bind" = loopback ] && [ "$PAPERCLIP_LOCAL" = 1 ] && paperclip_to_loopback "$pc_config"; then
      log "paperclip: instância migrada para só esta máquina, sem login (local_trusted)"
      with_node "paperclipai service install --json >/dev/null 2>&1 || true; paperclipai service restart >/dev/null 2>&1 || paperclipai service start >/dev/null 2>&1 || true"
    else
      with_node "paperclipai service install --json >/dev/null 2>&1 || true; paperclipai service start >/dev/null 2>&1 || true"
    fi
    log "paperclip: serviço presente e iniciado"
  fi
  # preparo automático só no modo local (nele a máquina é o operador; autenticado, exigiria login)
  if python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["server"].get("deploymentMode") != "local_trusted")' "$pc_config" 2>/dev/null; then
    paperclip_setup
  elif [ "$bind" = loopback ]; then
    log "paperclip: instância existente em modo autenticado; para só esta máquina, sem login: ./install.sh --paperclip-local"
  fi
  if [ "$WITH_HERMES" = 1 ] && have hermes; then
    if grep -q '^TELEGRAM_BOT_TOKEN=.' "$HERMES_HOME/.env" 2>/dev/null; then
      gw_present=0
      case "$OS" in
        Darwin) launchctl print "gui/$(id -u)/ai.hermes.gateway" >/dev/null 2>&1 && gw_present=1 ;;
        Linux) systemctl --user is-enabled hermes-gateway.service >/dev/null 2>&1 && gw_present=1 ;;
      esac
      if [ "$gw_present" = 0 ]; then
        hermes gateway install --start-on-login --start-now >>"$JARVIS_HOME/logs/instalacao-hermes.log" 2>&1 && log "hermes: gateway instalado como serviço"
      elif [ "${TELEGRAM_CHANGED:-0}" = 1 ] || [ "$HERMES_RESTART" = 1 ]; then
        hermes gateway restart >/dev/null 2>&1 || hermes gateway start >/dev/null 2>&1 || true
        log "hermes: gateway reiniciado (configuração mudou)"
      else
        log "hermes: gateway já instalado"
      fi
    else
      log "hermes: Telegram desligado (opcional; para ligar: ./install.sh --telegram)"
    fi
  fi
}

# JARVIS Web: a interface do produto. Clona o app na ref do lockfile, instala só as dependências de
# runtime (sem Electron), constrói a UI e registra um serviço de usuário que lê $JARVIS_HOME/profile.env.
install_web() {
  has_role desktop || return 0
  skipped web && { log "web: pulado"; return 0; }
  app="$JARVIS_HOME/app/jarvis-web"
  # Padrão: o pacote pronto publicado em whitebeard-os-releases (UI já construída, sha256 conferido), sem
  # npm, build nem acesso ao repositório do app. JARVIS_WEB_FROM_GIT=1: clone + build, para quem desenvolve.
  if [ -n "${JARVIS_WEB_URL:-}" ] && [ "${JARVIS_WEB_FROM_GIT:-0}" != 1 ]; then
    if [ "$DRY_RUN" = 1 ]; then log "web: baixaria o JARVIS Web $JARVIS_WEB_VERSION ($JARVIS_WEB_URL)"; return 0; fi
    [ -s "$NVM_DIR/nvm.sh" ] || die "web: Node ausente (etapa node pulada?)"
    dir="$JARVIS_HOME/app/jarvis-web-$JARVIS_WEB_VERSION"
    if [ ! -f "$dir/dist/index.html" ]; then
      log "web: baixando o JARVIS Web $JARVIS_WEB_VERSION"
      tmp=$(mktemp -d)
      fetch_verified "$JARVIS_WEB_URL" "$JARVIS_WEB_SHA256" "$tmp/web.tar.gz"
      if ! { tar -xzf "$tmp/web.tar.gz" -C "$tmp" && rm -rf "$dir" && mkdir -p "$JARVIS_HOME/app" \
        && mv "$tmp/jarvis-web-$JARVIS_WEB_VERSION" "$dir"; }; then die "web: pacote do JARVIS Web inválido"; fi
      rm -rf "$tmp"
    fi
    # instalação antiga por clone fica guardada, não é apagada
    if [ -d "$app" ] && [ ! -L "$app" ]; then rm -rf "$app-git"; mv "$app" "$app-git"; fi
    ln -sfn "$dir" "$app"
    node_bin=$(with_node 'command -v node')
    install_web_service "$app" "$node_bin"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "web: clonaria $JARVIS_WEB_REPO ($JARVIS_WEB_REF) em $app, npm ci sem dev, vite build, serviço jarvis-web na porta $JARVIS_WEB_PORT"
    return 0
  fi
  [ -s "$NVM_DIR/nvm.sh" ] || die "web: Node ausente (etapa node pulada?)"
  [ -L "$app" ] && rm -f "$app"
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
  # o serviço herda só o PATH do profile.env; sem a pasta do node (nvm), o Hermes chamado pelo app não acha
  # o npx dos MCPs stdio do tenant (painel de tools vazio). web.env = profile.env com o node à frente.
  web_env="$JARVIS_HOME/web.env"
  sed "s|^PATH=|PATH=$(dirname "$node_bin"):|" "$JARVIS_HOME/profile.env" >"$web_env"
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
EnvironmentFile=$web_env
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
      # enable --now não reinicia uma unidade já ativa: o processo antigo (outra versão, outro ExecStart)
      # continuaria no ar; restart garante que o que está rodando é o que a unidade descreve
      systemctl --user daemon-reload && systemctl --user enable jarvis-web.service >/dev/null 2>&1 \
        && systemctl --user restart jarvis-web.service && log "web: serviço jarvis-web ativo em $proto://localhost:$JARVIS_WEB_PORT"
      ;;
    Darwin)
      plist="$HOME/Library/LaunchAgents/dev.whitebeard.jarvis-web.plist"
      mkdir -p "$HOME/Library/LaunchAgents" "$JARVIS_HOME/logs"
      env_xml=$(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=\(.*\)$/      <key>\1<\/key><string>\2<\/string>/p' "$web_env")
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
      # bootout é assíncrono: bootstrap antes do serviço sair do domínio falha com "5: Input/output error"
      i=0
      while launchctl print "gui/$(id -u)/dev.whitebeard.jarvis-web" >/dev/null 2>&1 && [ "$i" -lt 20 ]; do
        i=$((i + 1)); sleep 0.5
      done
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
  if have claude; then
    if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then log "claude: conectado à sua conta"; else log "claude: NÃO conectado (rode: claude auth login)"; fi
  fi
  if has_role core && [ -s "$NVM_DIR/nvm.sh" ]; then log "paperclip: $(with_node 'paperclipai -V 2>/dev/null' | tail -n 1)"; fi
  if has_role knowledge && [ "$KNOWLEDGE_NATIVE" = 1 ]; then
    st=""; for s in ollama gbrain; do
      launchctl print "gui/$(id -u)/dev.whitebeard.$s" 2>/dev/null | grep -q "state = running" && st="$st $s=running" || st="$st $s=parado"
    done
    # com --no-start nada escuta na porta: o curl falha (saída 7) e, com set -e, derrubaria o resumo
    h=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3131/health 2>/dev/null || echo 000)
    tok=$(sed -n "s/^GBRAIN_MCP_TOKEN='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$JARVIS_HOME/secrets.env")
    m=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:3131/mcp 2>/dev/null || echo 000)
    unset tok
    c="não"; claude mcp get knowledge >/dev/null 2>&1 && c="sim"
    log "knowledge (nativo):$st | GBrain health=$h | MCP com token http=$m | MCP knowledge no Claude Code: $c"
    log "second brain: $(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env") (sincronizado a cada 2 min)"
  elif has_role knowledge && have docker; then log "knowledge: $(docker compose --project-directory "$JARVIS_HOME/compose/knowledge" ps --format '{{.Name}}={{.State}}' 2>/dev/null | tr '\n' ' ')"; fi
  if has_role desktop && [ -f "$JARVIS_HOME/app/jarvis-web/dist/index.html" ]; then
    p=http; [ -s "$JARVIS_HOME/tls/jarvis-web.crt" ] && p=https
    log "web: JARVIS Web em $p://localhost:$JARVIS_WEB_PORT (token em \$JARVIS_DATA/jarvis-web.json; certificado em \$JARVIS_HOME/tls)"
  fi
  if [ -f "$JARVIS_HOME/render/summary.txt" ]; then cat "$JARVIS_HOME/render/summary.txt"; fi
  [ "$KNOWLEDGE_ADIADO" = 1 ] && log "knowledge: adiado (sem Docker). Instale Docker e rode o bootstrap de novo"
  log "abra um terminal novo (ou: . \"$JARVIS_HOME/profile.sh\")"
  finalizar
}

# O que a pessoa precisa saber, em linguagem simples, e o JARVIS aberto no navegador.
finalizar() {
  [ "$DRY_RUN" = 1 ] || [ "$NO_START" = 1 ] && return 0
  has_role desktop || return 0
  data=$(sed -n 's/^JARVIS_DATA=//p' "$JARVIS_HOME/profile.env" 2>/dev/null)
  tok=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$data/jarvis-web.json" 2>/dev/null)
  [ -n "$tok" ] || return 0
  proto=http; [ -s "$JARVIS_HOME/tls/jarvis-web.crt" ] && proto=https
  url="$proto://localhost:$JARVIS_WEB_PORT/?token=$tok"
  printf '\n'
  printf '  Pronto! O JARVIS está no seu navegador:\n'
  printf '    %s\n' "$url"
  printf '  (se não abriu sozinho, copie o endereço acima; o aviso de "não seguro" é do certificado local)\n'
  if grep -q '^TELEGRAM_BOT_TOKEN=.' "$JARVIS_HOME/secrets.env" 2>/dev/null; then
    printf '  Telegram: ligado. Mande uma mensagem para o seu bot.\n'
  else
    printf '  Telegram: desligado. Para ligar: %s/install.sh --telegram\n' "$SCRIPT_DIR"
  fi
  printf '  Seu second brain: %s (abra no Obsidian; o JARVIS lê e escreve nele)\n' "$(sed -n 's/^VAULT_PATH=//p' "$JARVIS_HOME/profile.env" 2>/dev/null)"
  printf '  Para rodar de novo ou atualizar: o mesmo comando da instalação. Para desinstalar: %s/uninstall.sh\n\n' "$SCRIPT_DIR"
  if can_prompt; then
    case "$OS" in
      Darwin) open "$url" 2>/dev/null || true ;;
      Linux) if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && have xdg-open; then xdg-open "$url" >/dev/null 2>&1 || true; fi ;;
    esac
  fi
}

log "whitebeard-os $(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo dev) | $OS/$ARCH | papéis $ROLES$( [ "$DRY_RUN" = 1 ] && printf ' | dry-run' )$( [ "$NO_START" = 1 ] && printf ' | no-start' )"
check_prereqs
install_uv
install_node
install_claude
ensure_claude_login
install_ffmpeg
install_hermes
ensure_hermes_voice
install_paperclip
render_tenant
ensure_vault
apply_mcp
apply_claude_plugins
apply_hermes_config
apply_hermes_tenant
install_knowledge
apply_knowledge_render
install_web
install_services
install_rc
doctor
