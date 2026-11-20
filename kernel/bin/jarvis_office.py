#!/usr/bin/env python3
"""jarvis_office.py — prepara o Escritório no Paperclip local: cada cargo do catálogo (render/office.json)
vira um agente executado pelo Hermes (adapter hermes_local, perfil com o nome do cargo, ferramentas limitadas),
idempotente. Cargo que saiu do catálogo é pausado (nunca apagado). Grava office/positions.json ({name: {id, title}}).

Uso: jarvis_office.py --home <JARVIS_HOME> [--disable]
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
POSITION_NAME = "Assistente de trabalho"
PROFILE = "work"

# Regras de trabalho do cargo. Vão num AGENTS.md na pasta de trabalho: o Hermes injeta esse arquivo sozinho a
# cada execução (o Paperclip não aceita promptTemplate para agentes novos; o pedido da tarefa vem no prompt dele).
AGENTS_MD = """# Cargos do Escritório do Whitebeard OS

Você é um cargo do Escritório e executa a tarefa que o Paperclip indicou no pedido (número e ID da tarefa estão no prompt).
O que você faz e como entrega está na sua persona (SOUL.md); as regras abaixo valem para todos os cargos.

Como trabalhar:
1. Faça o trabalho pedido usando o que você tem: o second brain (ferramentas do cérebro), a web e os arquivos da pasta atual.
2. A entrega é SEMPRE um arquivo Markdown novo em `deliveries/<slug-curto>.md` (título, data, conteúdo em português do Brasil). Crie a pasta se não existir.
3. Ao terminar, chame a ferramenta `deliver` (MCP office) com o ID da tarefa, um resumo de duas ou três frases e o caminho do arquivo. É assim que a tarefa fica concluída; não use curl nem a API diretamente.
4. Se o pedido exigir uma ação fora deste computador (enviar e-mail ou mensagem a terceiros, pagar, publicar, comprar), NÃO faça: chame `request_approval` com a pergunta certa e pare.
5. Nunca invente dados. Se faltar informação para fazer bem, entregue o que dá com uma seção "O que faltou" e diga isso no resumo.
Não termine sem chamar `deliver` ou `request_approval`.
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


def as_list(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--disable", action="store_true", help="pausa o cargo (não apaga nada)")
    a = ap.parse_args()
    home = Path(a.home).expanduser()
    prof = read_env(home / "profile.env")
    vault = prof.get("VAULT_PATH") or str(Path.home() / "whitebeard-secondbrain")
    tenant = prof.get("JARVIS_TENANT") or "personal"
    hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    hermes_home = Path(prof.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    folder = Path(vault) / "tenants" / tenant / "projects-doc" / "office"
    try:
        comp = as_list(api("GET", "/companies"), "companies")
    except (urllib.error.URLError, OSError, RuntimeError):
        print("[whitebeard-os] escritório: o Paperclip não está no ar; o cargo fica para a próxima execução")
        return 0
    if not comp:
        print("[whitebeard-os] escritório: nenhuma empresa no Paperclip")
        return 0
    cid = comp[0]["id"]
    agents = as_list(api("GET", f"/companies/{cid}/agents"), "agents")
    render = {}
    try:
        render = json.loads((home / "render" / "office.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    positions = [c for c in (render.get("positions") or []) if isinstance(c, dict) and c.get("name")] if isinstance(render, dict) else []
    if not any(c.get("name") == PROFILE for c in positions):
        positions.insert(0, {"name": PROFILE, "title": POSITION_NAME, "does": ""})
    ids_path = home / "office" / "positions.json"
    try:
        ids = json.loads(ids_path.read_text(encoding="utf-8"))
        ids = ids if isinstance(ids, dict) else {}
    except (OSError, ValueError):
        ids = {}

    def agent_of(c: dict):
        reg = ids.get(c["name"]) or {}
        by_id = next((x for x in agents if x.get("id") == reg.get("id")), None) if reg.get("id") else None
        return by_id or next((x for x in agents if x.get("name") == (c.get("title") or c["name"]) and x.get("status") != "terminated"), None)

    if a.disable:
        for c in positions:
            position = agent_of(c)
            if position and position.get("status") != "paused":
                api("PATCH", f"/agents/{position['id']}", {"status": "paused"})
                print(f"[whitebeard-os] escritório: cargo '{c.get('title') or c['name']}' pausado")
        return 0
    (folder / "deliveries").mkdir(parents=True, exist_ok=True)
    agents_md = folder / "AGENTS.md"
    if not agents_md.exists() or agents_md.read_text(encoding="utf-8") != AGENTS_MD:
        agents_md.write_text(AGENTS_MD, encoding="utf-8")
    new_ids: dict = {}
    for c in positions:
        name, title = c["name"], (c.get("title") or c["name"])[:80]
        # modelo e provedor do perfil do cargo (config.yaml do Hermes): o adaptador manda -m/--provider; sem
        # eles o Hermes cai em "model: auto", que a Anthropic não conhece
        model_name, provider = "", ""
        try:
            block = (hermes_home / "profiles" / name / "config.yaml").read_text(encoding="utf-8")
            m = re.search(r"^model:\s*\n(?:\s+.*\n)*?\s+default:\s*([^\s#]+)", block, re.M)
            m_prov = re.search(r"^model:\s*\n(?:\s+.*\n)*?\s+provider:\s*([^\s#]+)", block, re.M)
            model_name, provider = (m.group(1) if m else ""), (m_prov.group(1) if m_prov else "")
        except OSError:
            print(f"[whitebeard-os] escritório: o perfil do Hermes do cargo {name} ainda não existe; fica para a próxima execução")
            continue
        cfg = {
            "hermesCommand": hermes,
            **({"model": model_name} if model_name else {}),
            **({"provider": provider} if provider else {}),
            # os MCPs do perfil entram pelo nome do servidor (office, knowledge); sem terminal
            "toolsets": "file,web,memory,skills,todo,office,knowledge",
            # HERMES_HOME é o que liga o `hermes chat` ao perfil (config, .env, SOUL.md, skills do cargo)
            "env": {"HERMES_HOME": str(hermes_home / "profiles" / name), "HERMES_PROFILE": name, "PAPERCLIP_API_URL": API, "OFFICE_POSITION_NAME": title},
            "cwd": str(folder),
            "persistSession": False,
            "timeoutSec": 900,
            "maxTurnsPerRun": 40,
        }
        position = agent_of(c)
        body = {"name": title, "role": "general", "adapterType": "hermes_local", "adapterConfig": cfg,
                 "runtimeConfig": {"heartbeat": {"enabled": False}}}
        if position is None:
            position = api("POST", f"/companies/{cid}/agents", body)
            print(f"[whitebeard-os] escritório: cargo '{title}' criado (executado pelo Hermes, perfil {name})")
        else:
            changed = (position.get("adapterType") != "hermes_local" or (position.get("adapterConfig") or {}) != cfg or position.get("status") == "paused"
                     or position.get("name") != title)
            if changed:
                api("PATCH", f"/agents/{position['id']}", {"name": title, "adapterType": "hermes_local", "adapterConfig": cfg, "status": "idle"})
                print(f"[whitebeard-os] escritório: cargo '{title}' atualizado")
        new_ids[name] = {"id": position["id"], "title": title, "company": cid}
    # cargos que saíram do catálogo: pausados (as tarefas e o histórico ficam)
    for name, reg in ids.items():
        if name in new_ids or not reg.get("id"):
            continue
        ag = next((x for x in agents if x.get("id") == reg["id"]), None)
        if ag and ag.get("status") not in ("paused", "terminated"):
            api("PATCH", f"/agents/{reg['id']}", {"status": "paused"})
            print(f"[whitebeard-os] escritório: cargo '{reg.get('title') or name}' saiu do catálogo; pausado")
        new_ids[name] = {**reg, "paused": True}
    (home / "office").mkdir(parents=True, exist_ok=True)
    ids_path.write_text(json.dumps(new_ids, ensure_ascii=False, indent=2), encoding="utf-8")
    if new_ids.get(PROFILE):
        (home / "office" / "position.json").write_text(json.dumps({"id": new_ids[PROFILE]["id"], "name": POSITION_NAME, "company": cid, "folder": str(folder)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
