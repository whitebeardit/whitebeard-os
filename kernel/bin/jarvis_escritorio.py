#!/usr/bin/env python3
"""jarvis_escritorio.py — prepara o Escritório no Paperclip local: o cargo "Assistente de trabalho" como agente
executado pelo Hermes (adapter hermes_local, perfil `trabalho`, ferramentas limitadas), idempotente.

Uso: jarvis_escritorio.py --home <JARVIS_HOME> [--desligar]
Lê profile.env (HERMES_HOME, VAULT_PATH, JARVIS_TENANT). Só biblioteca padrão. Nunca imprime segredo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
CARGO_NOME = "Assistente de trabalho"
PERFIL = "trabalho"

# Regras de trabalho do cargo. Vão num AGENTS.md na pasta de trabalho: o Hermes injeta esse arquivo sozinho a
# cada execução (o Paperclip não aceita promptTemplate para agentes novos; o pedido da tarefa vem no prompt dele).
AGENTS_MD = """# Assistente de trabalho (Escritório do Whitebeard OS)

Você executa a tarefa que o Paperclip indicou no pedido (número e ID da tarefa estão no prompt).

Como trabalhar:
1. Faça o trabalho pedido usando o que você tem: o second brain (ferramentas do cérebro), a web e os arquivos da pasta atual.
2. A entrega é SEMPRE um arquivo Markdown novo em `entregas/<slug-curto>.md` (título, data, conteúdo em português do Brasil). Crie a pasta se não existir.
3. Ao terminar, chame a ferramenta `entregar` (MCP escritorio) com o ID da tarefa, um resumo de duas ou três frases e o caminho do arquivo. É assim que a tarefa fica concluída; não use curl nem a API diretamente.
4. Se o pedido exigir uma ação fora deste computador (enviar e-mail ou mensagem a terceiros, pagar, publicar, comprar), NÃO faça: chame `pedir_aprovacao` com a pergunta certa e pare.
5. Nunca invente dados. Se faltar informação para fazer bem, entregue o que dá com uma seção "O que faltou" e diga isso no resumo.
Não termine sem chamar `entregar` ou `pedir_aprovacao`.
"""


def read_env(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Paperclip HTTP {e.code} em {method} {path}: {e.read().decode('utf-8', 'replace')[:400]}") from e


def lista(d, chave):
    return d if isinstance(d, list) else (d or {}).get(chave, [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--desligar", action="store_true", help="pausa o cargo (não apaga nada)")
    a = ap.parse_args()
    home = Path(a.home).expanduser()
    prof = read_env(home / "profile.env")
    vault = prof.get("VAULT_PATH") or str(Path.home() / "whitebeard-secondbrain")
    tenant = prof.get("JARVIS_TENANT") or "pessoal"
    hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    hermes_home = Path(prof.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    pasta = Path(vault) / "tenants" / tenant / "projects-doc" / "escritorio"
    try:
        comp = lista(api("GET", "/companies"), "companies")
    except (urllib.error.URLError, OSError, RuntimeError):
        print("[whitebeard-os] escritório: o Paperclip não está no ar; o cargo fica para a próxima execução")
        return 0
    if not comp:
        print("[whitebeard-os] escritório: nenhuma empresa no Paperclip")
        return 0
    cid = comp[0]["id"]
    agentes = lista(api("GET", f"/companies/{cid}/agents"), "agents")
    cargo = next((x for x in agentes if x.get("name") == CARGO_NOME), None)
    if a.desligar:
        if cargo and cargo.get("status") != "paused":
            api("PATCH", f"/agents/{cargo['id']}", {"status": "paused"})
            print("[whitebeard-os] escritório: cargo pausado")
        return 0
    (pasta / "entregas").mkdir(parents=True, exist_ok=True)
    # modelo e provedor do perfil do cargo (config.yaml do Hermes): o adaptador manda -m/--provider; sem
    # eles o Hermes cai em "model: auto", que a Anthropic não conhece
    modelo, provedor = "", ""
    try:
        bloco = (hermes_home / "profiles" / PERFIL / "config.yaml").read_text(encoding="utf-8")
        m = re.search(r"^model:\s*\n(?:\s+.*\n)*?\s+default:\s*([^\s#]+)", bloco, re.M)
        pv = re.search(r"^model:\s*\n(?:\s+.*\n)*?\s+provider:\s*([^\s#]+)", bloco, re.M)
        modelo, provedor = (m.group(1) if m else ""), (pv.group(1) if pv else "")
    except OSError:
        pass
    agents_md = pasta / "AGENTS.md"
    if not agents_md.exists() or agents_md.read_text(encoding="utf-8") != AGENTS_MD:
        agents_md.write_text(AGENTS_MD, encoding="utf-8")
    cfg = {
        "hermesCommand": hermes,
        **({"model": modelo} if modelo else {}),
        **({"provider": provedor} if provedor else {}),
        # os MCPs do perfil entram pelo nome do servidor (escritorio, knowledge); sem terminal
        "toolsets": "file,web,memory,skills,todo,escritorio,knowledge",
        # HERMES_HOME é o que liga o `hermes chat` ao perfil (config, .env, SOUL.md, skills do cargo)
        "env": {"HERMES_HOME": str(hermes_home / "profiles" / PERFIL), "HERMES_PROFILE": PERFIL, "PAPERCLIP_API_URL": API, "ESCRITORIO_CARGO_NOME": CARGO_NOME},
        "cwd": str(pasta),
        "persistSession": False,
        "timeoutSec": 900,
        "maxTurnsPerRun": 40,
    }
    corpo = {"name": CARGO_NOME, "role": "general", "adapterType": "hermes_local", "adapterConfig": cfg,
             "runtimeConfig": {"heartbeat": {"enabled": False}}}
    if cargo is None:
        cargo = api("POST", f"/companies/{cid}/agents", corpo)
        print(f"[whitebeard-os] escritório: cargo '{CARGO_NOME}' criado (executado pelo Hermes, perfil {PERFIL})")
    else:
        mudou = (cargo.get("adapterType") != "hermes_local" or (cargo.get("adapterConfig") or {}) != cfg or cargo.get("status") == "paused")
        if mudou:
            api("PATCH", f"/agents/{cargo['id']}", {"adapterType": "hermes_local", "adapterConfig": cfg, "status": "idle"})
            print(f"[whitebeard-os] escritório: cargo '{CARGO_NOME}' atualizado")
    (home / "escritorio").mkdir(parents=True, exist_ok=True)
    (home / "escritorio" / "cargo.json").write_text(json.dumps({"id": cargo["id"], "nome": CARGO_NOME, "empresa": cid, "pasta": str(pasta)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
