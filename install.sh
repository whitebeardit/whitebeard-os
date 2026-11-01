#!/bin/sh
# install.sh — instala o Whitebeard OS numa máquina zerada com um comando (Linux x86_64 ou macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/whitebeardit/whitebeard-os/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --with-hermes        # repassa opções ao bootstrap
#
# Variáveis de ambiente (todas opcionais):
#   WHITEBEARD_OS_DIR    onde clonar (padrão: ~/DEV/whitebeard-os)
#   WHITEBEARD_OS_REPO   URL git do kernel (padrão: https://github.com/whitebeardit/whitebeard-os.git;
#                    enquanto o repositório for privado use git@github.com:whitebeardit/whitebeard-os.git)
#   WHITEBEARD_OS_REF    branch ou tag (padrão: main)
#   JARVIS_TENANT    tenant: nome (tenants/<nome>/ do kernel), pasta ou caminho do jarvis.yaml
#                    (padrão: exemplo). O profile.sh exporta o nome do tenant instalado, então
#                    rodar de novo num terminal com o profile carregado reinstala o mesmo tenant.
#
# O que faz: garante git e curl (no macOS instala as Command Line Tools), clona ou atualiza o kernel e
# chama bootstrap.sh; os papéis vêm de `nos:` do tenant (tudo-em-um por padrão). Idempotente: pode rodar de novo.
set -eu

WHITEBEARD_OS_DIR="${WHITEBEARD_OS_DIR:-$HOME/DEV/whitebeard-os}"
WHITEBEARD_OS_REPO="${WHITEBEARD_OS_REPO:-https://github.com/whitebeardit/whitebeard-os.git}"
WHITEBEARD_OS_REF="${WHITEBEARD_OS_REF:-main}"
JARVIS_TENANT="${JARVIS_TENANT:-}"

log() { printf '[whitebeard-os/install] %s\n' "$*"; }
die() { printf '[whitebeard-os/install] ERRO: %s\n' "$*" >&2; exit 1; }
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

if [ -d "$WHITEBEARD_OS_DIR/.git" ]; then
  log "atualizando $WHITEBEARD_OS_DIR ($WHITEBEARD_OS_REF)"
  git -C "$WHITEBEARD_OS_DIR" fetch -q origin "$WHITEBEARD_OS_REF"
  git -C "$WHITEBEARD_OS_DIR" checkout -q "$WHITEBEARD_OS_REF"
  git -C "$WHITEBEARD_OS_DIR" merge -q --ff-only "origin/$WHITEBEARD_OS_REF"
else
  log "clonando $WHITEBEARD_OS_REPO ($WHITEBEARD_OS_REF) em $WHITEBEARD_OS_DIR"
  mkdir -p "$(dirname "$WHITEBEARD_OS_DIR")"
  if ! git clone -q --branch "$WHITEBEARD_OS_REF" "$WHITEBEARD_OS_REPO" "$WHITEBEARD_OS_DIR"; then
    die "clone falhou. Repositório privado? Use WHITEBEARD_OS_REPO=git@github.com:whitebeardit/whitebeard-os.git com sua chave SSH no GitHub"
  fi
fi

tenant="${JARVIS_TENANT:-exemplo}"
if [ -f "$tenant" ]; then JARVIS_TENANT="$tenant"
elif [ -f "$tenant/jarvis.yaml" ]; then JARVIS_TENANT="$tenant/jarvis.yaml"
else JARVIS_TENANT="$WHITEBEARD_OS_DIR/tenants/$tenant/jarvis.yaml"; fi
[ -f "$JARVIS_TENANT" ] || die "tenant não encontrado: $tenant (nome em tenants/, pasta ou caminho do jarvis.yaml)"

log "bootstrap: papel desktop, tenant $JARVIS_TENANT"
# stdin de /dev/null: quando este script vem por `curl | sh`, nada abaixo pode ler o próprio script.
exec sh "$WHITEBEARD_OS_DIR/bootstrap.sh" --tenant "$JARVIS_TENANT" "$@" </dev/null
