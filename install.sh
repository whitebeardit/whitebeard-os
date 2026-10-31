#!/bin/sh
# install.sh — instala o JARVIS OS numa máquina zerada com um comando (Linux x86_64 ou macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/whitebeardit/jarvis-os/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --with-hermes        # repassa opções ao bootstrap
#
# Variáveis de ambiente (todas opcionais):
#   JARVIS_OS_DIR    onde clonar (padrão: ~/DEV/jarvis-os)
#   JARVIS_OS_REPO   URL git do kernel (padrão: https://github.com/whitebeardit/jarvis-os.git;
#                    enquanto o repositório for privado use git@github.com:whitebeardit/jarvis-os.git)
#   JARVIS_OS_REF    branch ou tag (padrão: main)
#   JARVIS_TENANT    caminho do jarvis.yaml (padrão: tenants/exemplo/jarvis.yaml do próprio kernel)
#
# O que faz: garante git e curl (no macOS instala as Command Line Tools), clona ou atualiza o kernel e
# chama bootstrap.sh com o papel desktop. Idempotente: pode rodar de novo.
set -eu

JARVIS_OS_DIR="${JARVIS_OS_DIR:-$HOME/DEV/jarvis-os}"
JARVIS_OS_REPO="${JARVIS_OS_REPO:-https://github.com/whitebeardit/jarvis-os.git}"
JARVIS_OS_REF="${JARVIS_OS_REF:-main}"
JARVIS_TENANT="${JARVIS_TENANT:-}"

log() { printf '[jarvis-os/install] %s\n' "$*"; }
die() { printf '[jarvis-os/install] ERRO: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS=$(uname -s)
case "$OS" in
  Linux|Darwin) ;;
  *) die "sistema não suportado: $OS (alvos: Linux, macOS)" ;;
esac

# macOS zerado: git e python3 vêm das Command Line Tools. Abre o diálogo do sistema e espera.
if [ "$OS" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
  log "instalando as Command Line Tools do Xcode: confirme o diálogo que o macOS vai abrir"
  xcode-select --install >/dev/null 2>&1 || true
  i=0
  until xcode-select -p >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 180 ]; then die "Command Line Tools não instaladas em 30 min; rode xcode-select --install e tente de novo"; fi
    sleep 10
  done
  log "Command Line Tools prontas"
fi

for t in git curl; do
  if ! have "$t"; then
    die "falta $t. Ubuntu/Debian: sudo apt-get install -y git curl python3 ca-certificates"
  fi
done

if [ -d "$JARVIS_OS_DIR/.git" ]; then
  log "atualizando $JARVIS_OS_DIR ($JARVIS_OS_REF)"
  git -C "$JARVIS_OS_DIR" fetch -q origin "$JARVIS_OS_REF"
  git -C "$JARVIS_OS_DIR" checkout -q "$JARVIS_OS_REF"
  git -C "$JARVIS_OS_DIR" merge -q --ff-only "origin/$JARVIS_OS_REF"
else
  log "clonando $JARVIS_OS_REPO ($JARVIS_OS_REF) em $JARVIS_OS_DIR"
  mkdir -p "$(dirname "$JARVIS_OS_DIR")"
  if ! git clone -q --branch "$JARVIS_OS_REF" "$JARVIS_OS_REPO" "$JARVIS_OS_DIR"; then
    die "clone falhou. Repositório privado? Use JARVIS_OS_REPO=git@github.com:whitebeardit/jarvis-os.git com sua chave SSH no GitHub"
  fi
fi

if [ -z "$JARVIS_TENANT" ]; then JARVIS_TENANT="$JARVIS_OS_DIR/tenants/exemplo/jarvis.yaml"; fi
[ -f "$JARVIS_TENANT" ] || die "tenant não encontrado: $JARVIS_TENANT"

log "bootstrap: papel desktop, tenant $JARVIS_TENANT"
# stdin de /dev/null: quando este script vem por `curl | sh`, nada abaixo pode ler o próprio script.
exec sh "$JARVIS_OS_DIR/bootstrap.sh" --tenant "$JARVIS_TENANT" "$@" </dev/null
