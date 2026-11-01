#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7", "jsonschema>=4,<5"]
# ///
"""Renderiza a instalação de um tenant do Whitebeard OS a partir do jarvis.yaml.

Entrada: jarvis.yaml (declaração do tenant), manifesto de segredos e lockfile do kernel, papéis do nó.
Saída em $JARVIS_HOME: jarvis.yaml (cópia), secrets.env (modo 600), profile.sh, skills/ (copiadas e
linkadas em ~/.claude/skills) e, em render/: claude-mcp.sh (desktop), knowledge.env (knowledge),
hermes/config.yaml e hermes/env (core), summary.txt. Nunca imprime valor de segredo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets as pysecrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROLES = ("desktop", "core", "satelite", "knowledge")
VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def log(msg: str) -> None:
    print(f"[render] {msg}")


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def sq(text: str) -> str:
    """Aspas simples para sh (sem expansão)."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


def dq(text: str) -> str:
    """Aspas duplas para sh (mantém expansão de ${VAR})."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Writer:
    def __init__(self, dry: bool) -> None:
        self.dry = dry

    def write(self, path: Path, content: str, mode: int = 0o644) -> None:
        if self.dry:
            log(f"escreveria {path} ({len(content.splitlines())} linhas, modo {oct(mode)[2:]})")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="caminho do jarvis.yaml")
    ap.add_argument("--role", default="desktop", help="papéis separados por vírgula: desktop,core,knowledge")
    ap.add_argument("--home", required=True, help="$JARVIS_HOME")
    ap.add_argument("--kernel", required=True, help="pasta kernel/ do repositório")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    roles = [r.strip() for r in args.role.split(",") if r.strip()]
    bad = [r for r in roles if r not in ROLES]
    if bad or not roles:
        print(f"[render] papéis inválidos: {', '.join(bad) or '(vazio)'}", file=sys.stderr)
        return 2

    def for_roles(item: dict) -> bool:
        return any(r in item.get("roles", list(ROLES)) for r in roles)

    tenant_path = Path(args.tenant).expanduser().resolve()
    tenant_dir = tenant_path.parent
    kernel = Path(args.kernel).expanduser().resolve()
    home = Path(args.home).expanduser()
    user_home = Path.home()
    w = Writer(args.dry_run)
    versions = parse_env(kernel / "versions.env")

    data = yaml.safe_load(tenant_path.read_text(encoding="utf-8")) or {}
    schema = json.loads((kernel / "schema" / "jarvis.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        for err in errors:
            where = "/".join(str(p) for p in err.absolute_path) or "(raiz)"
            print(f"[render] jarvis.yaml inválido em {where}: {err.message}", file=sys.stderr)
        return 2
    tenant = data["tenant"]
    role_label = ",".join(roles)
    log(f"tenant={tenant} papéis={role_label} home={home}" + (" (dry-run)" if args.dry_run else ""))

    # ---- segredos: manifesto do kernel (por papel) + declarados no tenant + referenciados como ${X}
    manifest = yaml.safe_load((kernel / "secrets" / "manifest.yaml").read_text(encoding="utf-8"))
    entries: list[dict] = [dict(s) for s in manifest.get("secrets", []) if for_roles(s)]
    for s in data.get("segredos", []) or []:
        if for_roles(s):
            entries.append(dict(s))
    known = {s["name"] for s in entries}
    referenced = set(VAR_RE.findall(yaml.safe_dump(data, allow_unicode=True)))
    for name in sorted(referenced - known):
        entries.append({
            "name": name,
            "description": "referenciada no jarvis.yaml como ${...}; declare em `segredos:` para documentar",
            "required": False,
        })
    secrets_path = home / "secrets.env"
    current = parse_env(secrets_path)
    values = {s["name"]: (current.get(s["name"]) or os.environ.get(s["name"], "")) for s in entries}
    generated: list[str] = []
    for s in entries:
        if s.get("generate") and not values[s["name"]]:
            values[s["name"]] = pysecrets.token_urlsafe(24)
            generated.append(s["name"])
    missing = [s["name"] for s in entries if s.get("required") and not values[s["name"]]]
    empty = [s["name"] for s in entries if not values[s["name"]]]

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# secrets.env gerado pelo whitebeard-os. Modo 600. Nunca commitar, nunca copiar entre máquinas.",
        f"# tenant={tenant} papéis={role_label} gerado_em={stamp}",
        "# Preencha os valores vazios e rode o bootstrap de novo para aplicar.",
    ]
    for s in entries:
        name = s["name"]
        lines.append("")
        if s.get("description"):
            lines.append(f"# {s['description']}")
        if s.get("obtain"):
            lines.append(f"# como obter: {s['obtain']}")
        lines.append(f"# obrigatório: {'sim' if s.get('required') else 'não'}")
        lines.append(f"{name}={sq(values[name]) if values[name] else ''}")
    w.write(secrets_path, "\n".join(lines) + "\n", mode=0o600)

    # ---- cópia da declaração
    w.write(home / "jarvis.yaml", tenant_path.read_text(encoding="utf-8"))
    w.write(home / "tenant.path", str(tenant_path) + "\n")

    # ---- profile.sh
    vault_path = None
    vault = data.get("vault") or {}
    if vault.get("path"):
        vault_path = str(user_home / vault["path"])
    # pasta de dados do tenant: `dados` (relativa a $HOME) ou $JARVIS_HOME/data; o JARVIS Web e as
    # skills leem $JARVIS_DATA, nunca um nome fixo
    data_dir = str(user_home / data["dados"]) if data.get("dados") else str(home / "data")
    if not args.dry_run:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
    profile = [
        f"# gerado pelo whitebeard-os para o tenant {tenant}; não editar (re-rode o bootstrap)",
        f"export JARVIS_HOME={sq(str(home))}",
        f"export JARVIS_TENANT={sq(tenant)}",
        f"export JARVIS_ROLE={sq(role_label)}",
        'export JARVIS_DATA="${JARVIS_DATA:-' + data_dir + '}"',
    ]
    if vault_path:
        profile.append('export VAULT_PATH="${VAULT_PATH:-' + vault_path + '}"')
    profile += [
        'export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"',
        'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac',
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"',
        'if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi',
    ]
    w.write(home / "profile.sh", "\n".join(profile) + "\n")

    # ---- servidores MCP do Claude Code (papel desktop; segredos só por ${VAR} em runtime)
    servers = [m for m in data.get("mcp", []) or [] if for_roles(m)] if "desktop" in roles else []
    if servers:
        mcp = [
            "#!/bin/sh",
            f"# gerado pelo whitebeard-os a partir de {tenant_path}; idempotente",
            "set -eu",
            "set -a",
            f". {sq(str(secrets_path))}",
            "set +a",
            "",
        ]
        for m in servers:
            name = m["name"]
            mcp.append(f"claude mcp remove --scope user {sq(name)} >/dev/null 2>&1 || true")
            # Ordem importa: --env e --header são variádicos (engolem o que vier depois até a próxima
            # opção ou "--"), então vêm depois dos posicionais nome/url e antes de "--".
            cmd = ["claude", "mcp", "add", "--scope", "user", "--transport", m["transport"], sq(name)]
            if m["transport"] != "stdio":
                cmd.append(sq(m["url"]))
            for k, v in (m.get("headers") or {}).items():
                cmd += ["--header", dq(f"{k}: {v}")]
            for k, v in (m.get("env") or {}).items():
                cmd += ["--env", dq(f"{k}={v}")]
            if m["transport"] == "stdio":
                cmd += ["--", sq(m["command"])] + [sq(a) for a in m.get("args", []) or []]
            mcp.append(" ".join(cmd))
            mcp.append(f"printf '[whitebeard-os] mcp %s registrado\\n' {sq(name)}")
        w.write(home / "render" / "claude-mcp.sh", "\n".join(mcp) + "\n", mode=0o700)

    # ---- papel core: Hermes (config inicial e variáveis), aplicados pelo bootstrap sem sobrescrever
    hermes_cfg = data.get("hermes") or {}
    if "core" in roles or hermes_cfg:
        if hermes_cfg:
            model = {k: hermes_cfg[k] for k in ("provider", "model") if k in hermes_cfg}
            cfg = {"model": model}
            if hermes_cfg.get("fallback"):
                cfg["fallback_model"] = hermes_cfg["fallback"]
            w.write(home / "render" / "hermes" / "config.yaml",
                    "# gerado pelo whitebeard-os (modelo do tenant); o Hermes completa o resto\n"
                    + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        env_keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
        env_lines = [f"{k}={values[k]}" for k in env_keys if values.get(k)]
        w.write(home / "render" / "hermes" / "env",
                "# gerado pelo whitebeard-os; só chaves preenchidas em secrets.env\n" + "\n".join(env_lines) + "\n",
                mode=0o600)

    # ---- papel knowledge: .env do compose (imagens do lockfile + segredos gerados)
    if "knowledge" in roles:
        if vault_path and Path(vault_path).is_dir():
            vault_index = Path(vault_path)
        else:
            vault_index = home / "vault-index"
            if not args.dry_run:
                vault_index.mkdir(parents=True, exist_ok=True)
        kenv = {
            "PGVECTOR_IMAGE": versions.get("PGVECTOR_IMAGE", "pgvector/pgvector:pg17"),
            "OLLAMA_IMAGE": versions.get("OLLAMA_IMAGE", "ollama/ollama:latest"),
            "BUN_IMAGE": versions.get("BUN_IMAGE", "oven/bun:1.4"),
            "GBRAIN_REF": versions.get("GBRAIN_REF", "master"),
            "EMBED_MODEL": versions.get("EMBED_MODEL", "bge-m3"),
            "POSTGRES_PASSWORD": values.get("POSTGRES_PASSWORD", ""),
            "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": values.get("GBRAIN_ADMIN_BOOTSTRAP_TOKEN", ""),
            "ANTHROPIC_API_KEY": values.get("ANTHROPIC_API_KEY", ""),
            "GBRAIN_PUBLIC_URL": "http://localhost:3131",
            "GBRAIN_PORT": "3131",
            "OLLAMA_PORT": "11434",
            "VAULT_INDEX_DIR": str(vault_index),
            "ACELERACAO": str(data.get("aceleracao", "auto")),
        }
        w.write(home / "render" / "knowledge.env",
                "# gerado pelo whitebeard-os para docker compose; modo 600\n"
                + "\n".join(f"{k}={v}" for k, v in kenv.items()) + "\n", mode=0o600)

    # ---- skills: kernel + packs do tenant → $JARVIS_HOME/skills, linkadas em ~/.claude/skills
    packs = [kernel / "skills"] + [
        (tenant_dir / p).resolve() for p in (data.get("skills") or {}).get("packs", []) or []
    ]
    installed: list[str] = []
    not_linked: list[str] = []
    claude_skills = user_home / ".claude" / "skills"
    for pack in packs:
        if not pack.is_dir():
            log(f"pack de skills ausente: {pack}")
            continue
        for d in sorted(pack.iterdir()):
            if d.name.startswith(".") or not (d / "SKILL.md").is_file():
                continue
            dest = home / "skills" / d.name
            link = claude_skills / d.name
            if args.dry_run:
                log(f"instalaria skill {d.name} <- {d}")
                installed.append(d.name)
                continue
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            elif dest.is_dir():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(d, dest)
            claude_skills.mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                link.unlink()
            if link.exists():
                not_linked.append(str(link))
            else:
                link.symlink_to(dest)
            installed.append(d.name)

    # ---- resumo
    summary = [
        f"[whitebeard-os] tenant: {tenant} | papéis: {role_label} | fonte: {tenant_path}",
        f"[whitebeard-os] skills instaladas ({len(installed)}): {', '.join(installed) or '-'}",
        f"[whitebeard-os] mcp ({len(servers)}): {', '.join(m['name'] for m in servers) or '-'}",
        f"[whitebeard-os] segredos: {len(entries)} esperados, {len(empty)} vazios"
        + (f", {len(generated)} gerados ({', '.join(generated)})" if generated else "")
        + (f"; OBRIGATÓRIOS faltando: {', '.join(missing)}" if missing else ""),
    ]
    if vault_path:
        summary.append(f"[whitebeard-os] vault: {vault_path}" + ("" if Path(vault_path).exists() else " (ainda não clonado)"))
    summary.append(f"[whitebeard-os] dados: {data_dir}")
    if "knowledge" in roles:
        summary.append(f"[whitebeard-os] knowledge: compose em {home / 'compose' / 'knowledge'}; GBrain {versions.get('GBRAIN_REF', '?')}, embeddings {versions.get('EMBED_MODEL', '?')}")
    if not_linked:
        summary.append(f"[whitebeard-os] aviso: não linkei (destino existe e não é link): {', '.join(not_linked)}")
    if missing:
        summary.append(f"[whitebeard-os] preencha {secrets_path} e rode o bootstrap de novo")
    w.write(home / "render" / "summary.txt", "\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
