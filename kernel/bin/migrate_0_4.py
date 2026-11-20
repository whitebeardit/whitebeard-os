#!/usr/bin/env python3
"""migrate_0_4.py — leva uma instalação de antes da 0.4.0 para os nomes em inglês (breaking change, sem compatibilidade).

O que faz (e imprime cada mudança):
  $JARVIS_HOME   renomeia os arquivos de estado (assistentes.json → assistants.json, escritorio.json → office.json,
                 escritorio/ → office/ com cargos.json → positions.json, rotinas.json → routines.json, carteiro.json →
                 courier.json; pensa*.json → thinker*.json; telegram.decisao → telegram.decision; assistentes/ →
                 assistants/; render/hermes/cargos → positions, modelo.yaml → model.yaml…) e traduz as chaves e os
                 valores de enum dentro deles; troca GITHUB_LEITURA_TOKEN por GITHUB_READ_TOKEN no secrets.env
                 (o valor fica); acerta tenant.path e addons.json que apontavam para tenants/pessoal ou tenants/exemplo.
  $JARVIS_DATA   preferencias.json → preferences.json (--data).
  jarvis.yaml    reescreve as chaves e os valores de enum, linha a linha, mantendo os comentários; guarda antes uma
                 cópia .bak (--tenant).
  addon.yaml     o mesmo para um add-on (--addon, repetível): descricao → description, segredos → secrets.

Idempotente: rodar de novo não muda nada. Nunca sobrescreve: se o nome antigo e o novo existem os dois, deixa os dois
como estão e avisa. Caches que se refazem sozinhos (tokens do Escritório) são apagados. Só biblioteca padrão.

Uso: python3 kernel/bin/migrate_0_4.py --home ~/.jarvis [--data ~/.whitebeard] [--tenant <jarvis.yaml>]
                                       [--addon <addon.yaml>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- mapas (os mesmos nomes que o kernel usa desde a 0.4.0)
YAML_KEYS = {
    "idioma": "language", "aceleracao": "acceleration", "dados": "data", "modulos": "modules", "segredos": "secrets",
    "decisoes": "decisions", "perfis": "profiles", "perfis_excluidos": "excluded_profiles",
    "servicos_monitorados": "monitored_services", "nos": "nodes", "papel": "role", "papeis": "roles",
    "assistentes": "assistants", "escritorio": "office", "cargos": "positions", "cargo": "position", "rotinas": "routines",
    "exportacao": "export", "nome": "name", "descricao": "description", "tipo": "type", "canal": "channel",
    "ajustes": "settings", "computador": "computer", "anotar": "write_notes", "nivel": "level", "provedor": "provider",
    "modelo": "model", "whatsapp_modo": "whatsapp_mode", "perfil": "profile", "quando": "when", "agenda": "schedule",
    "faz": "does", "titulo": "title", "onde": "where", "ligado": "enabled", "ligada": "enabled", "grupos": "groups",
    "limiar_confianca": "confidence_threshold",
}
MODULES = {"sistemas": "systems", "conhecimento": "knowledge", "arquivos": "files", "comunicacoes": "communications",
           "ferramentas": "tools", "diagnosticos": "diagnostics", "operacao": "operations", "midias": "media",
           "rastreamento": "tracking"}
ASSISTANT_TYPES = {"mim": "owner", "equipe": "team", "cliente": "client", "rotinas": "routines"}
PROFILE_TYPES = {"pessoal": "personal", "grupo-cliente": "client-group", "grupo-interno": "internal-group", "servico": "service"}
LEVELS = {"rapido": "fast", "equilibrado": "balanced", "capaz": "capable"}
ROLES = {"satelite": "satellite"}
DECISION_PROVIDERS = {"regras": "rules"}
POSITIONS = {"trabalho": "work", "coordenacao": "coordination"}
ROUTINES = {"resumo-da-semana": "weekly-summary"}
TENANTS = {"pessoal": "personal", "exemplo": "example"}
STATES = {"preparando": "preparing", "esperando": "waiting", "conectado": "connected", "erro": "error",
          "expirado": "expired", "cancelado": "cancelled", "nenhum": "none"}
NOTICES = {"pendencia": "pending"}


def where_value(v):
    return "assistant:" + v[len("assistente:"):] if isinstance(v, str) and v.startswith("assistente:") else v


# ---------------------------------------------------------------- registro
class Run:
    def __init__(self, dry: bool) -> None:
        self.dry = dry
        self.changes = 0
        self.warnings = 0

    def say(self, msg: str) -> None:
        self.changes += 1
        print(f"[migrate] {'(dry-run) ' if self.dry else ''}{msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"[migrate] aviso: {msg}")


# ---------------------------------------------------------------- JSON: conversões por arquivo
def keys(d: dict, mapping: dict) -> dict:
    return {mapping.get(k, k): v for k, v in d.items()}


def conv_assistant(a):
    """Um assistente da loja (assistants.json) ou do catálogo do render."""
    if not isinstance(a, dict):
        return a
    a = keys(a, {"nome": "name", "tipo": "type", "canal": "channel", "descricao": "description", "ajustes": "settings",
                 "nivel": "level", "provedor": "provider", "whatsapp_modo": "whatsapp_mode", "criado_em": "created_at",
                 "pessoas_nomes": "people_names", "grupos_nomes": "group_names", "removido": "removed",
                 "do_pacote": "from_pack", "segredos": "secrets", "pronto": "ready"})
    if a.get("type") in ASSISTANT_TYPES:
        a["type"] = ASSISTANT_TYPES[a["type"]]
    if a.get("level") in LEVELS:
        a["level"] = LEVELS[a["level"]]
    if isinstance(a.get("settings"), dict):
        a["settings"] = keys(a["settings"], {"computador": "computer", "anotar": "write_notes"})
    if isinstance(a.get("secrets"), dict):
        a["secrets"] = keys(a["secrets"], {"pessoas": "people", "grupos": "groups"})
    if isinstance(a.get("whatsapp"), dict):
        a["whatsapp"] = keys(a["whatsapp"], {"modo": "mode", "sessao": "session", "telefone": "phone"})
        if isinstance(a["whatsapp"].get("session"), str):
            a["whatsapp"]["session"] = a["whatsapp"]["session"].replace("/assistentes/", "/assistants/")
    return a


def conv_assistants(d):
    return [conv_assistant(a) for a in d] if isinstance(d, list) else d


def conv_position(c):
    if not isinstance(c, dict):
        return c
    c = keys(c, {"nome": "name", "titulo": "title", "faz": "does", "nivel": "level", "removido": "removed"})
    if c.get("level") in LEVELS:
        c["level"] = LEVELS[c["level"]]
    if c.get("name") in POSITIONS:
        c["name"] = POSITIONS[c["name"]]
    return c


def conv_routine(r):
    if not isinstance(r, dict):
        return r
    r = keys(r, {"nome": "name", "titulo": "title", "faz": "does", "quando": "when", "onde": "where", "cargo": "position",
                 "ligada": "enabled", "removido": "removed"})
    if r.get("name") in ROUTINES:
        r["name"] = ROUTINES[r["name"]]
    if r.get("position") in POSITIONS:
        r["position"] = POSITIONS[r["position"]]
    if "where" in r:
        r["where"] = where_value(r["where"])
    return r


def conv_office(d):
    """office.json (loja da página) e render/office.json: {ligado, cargos, rotinas}."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"ligado": "enabled", "cargos": "positions", "rotinas": "routines"})
    if isinstance(d.get("positions"), list):
        d["positions"] = [conv_position(c) for c in d["positions"]]
    if isinstance(d.get("routines"), list):
        d["routines"] = [conv_routine(r) for r in d["routines"]]
    return d


def conv_routines(d):
    return [conv_routine(r) for r in d] if isinstance(d, list) else d


def conv_position_ids(d):
    """office/positions.json: {nome: {id, titulo, empresa, pausado}}."""
    if not isinstance(d, dict):
        return d
    return {POSITIONS.get(k, k): (keys(v, {"titulo": "title", "empresa": "company", "pausado": "paused"}) if isinstance(v, dict) else v)
            for k, v in d.items()}


def conv_position_legacy(d):
    """office/position.json (só o cargo padrão, instalações antigas): {id, nome, empresa, pasta}."""
    return keys(d, {"nome": "name", "empresa": "company", "pasta": "folder"}) if isinstance(d, dict) else d


def conv_courier(d):
    """office/courier.json: {avisadas, inicio, rotinas: {nome: {ultima, tarefa, em, manual}}}."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"avisadas": "notified", "inicio": "started_at", "rotinas": "routines"})
    if isinstance(d.get("notified"), dict):
        d["notified"] = {k: NOTICES.get(v, v) if isinstance(v, str) else v for k, v in d["notified"].items()}
    if isinstance(d.get("routines"), dict):
        d["routines"] = {ROUTINES.get(k, k): (keys(v, {"ultima": "last", "tarefa": "task", "em": "at"}) if isinstance(v, dict) else v)
                         for k, v in d["routines"].items()}
    return d


def conv_thinker(d):
    """thinker.json: {provedor, nivel, logados}."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"provedor": "provider", "nivel": "level", "logados": "logged_in"})
    if d.get("level") in LEVELS:
        d["level"] = LEVELS[d["level"]]
    return d


def conv_thinker_cache(d):
    if not isinstance(d, dict):
        return d
    return {k: (keys(v, {"logado": "logged_in", "em": "at"}) if isinstance(v, dict) else v) for k, v in d.items()}


def conv_login(d):
    """thinker.login.json e assistants/<nome>.whatsapp.json (estado de um login ou pareamento em curso)."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"provedor": "provider", "estado": "state", "inicio": "started_at", "endereco": "url", "codigo": "code",
                 "erro": "error", "telefone": "phone", "nome": "name", "atualizado_em": "updated_at"})
    if d.get("state") in STATES:
        d["state"] = STATES[d["state"]]
    return d


def conv_update(d):
    """update.json (cache da consulta) e update.run.json (a atualização em curso)."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"verificado": "checked_at", "remoto": "remote", "notas": "notes", "erro": "error", "detalhe": "detail",
                 "inicio": "started_at", "de": "from", "unidade": "unit"})
    if isinstance(d.get("from"), dict):
        d["from"] = keys(d["from"], {"ramo": "branch"})
    return d


def conv_secrets_catalog(d):
    """render/secrets.json."""
    m = {"nome": "name", "titulo": "title", "descricao": "description", "obter": "obtain", "obrigatorio": "required",
         "gerado": "generated", "implicito": "implicit", "assistente": "assistant"}
    return [keys(s, m) if isinstance(s, dict) else s for s in d] if isinstance(d, list) else d


def conv_web(d):
    """render/web.json: {tenant, modulos}."""
    if not isinstance(d, dict):
        return d
    d = keys(d, {"modulos": "modules"})
    if isinstance(d.get("modules"), list):
        d["modules"] = [MODULES.get(x, x) for x in d["modules"]]
    if d.get("tenant") in TENANTS:
        d["tenant"] = TENANTS[d["tenant"]]
    return d


PREFERENCE_KEYS = {"idioma": "language", "tema": "theme", "escalaTexto": "text_scale"}


def conv_preferences(d):
    """preferences.json ($JARVIS_DATA) é do JARVIS Web: idioma, tema e tamanho do texto."""
    if isinstance(d, dict):
        return {PREFERENCE_KEYS.get(k, k): v for k, v in d.items()}
    return d


# ---------------------------------------------------------------- operações de arquivo
def move(run: Run, old: Path, new: Path) -> Path | None:
    """Renomeia old → new. Devolve o caminho que vale agora (new), ou None se não há nada."""
    if old.exists() or old.is_symlink():
        if new.exists() or new.is_symlink():
            run.warn(f"{old} e {new} existem os dois; nada foi mexido (apague o que não vale e rode de novo)")
            return None
        run.say(f"renomeado: {old} → {new}")
        if not run.dry:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            return new
        return old  # dry-run: a conversão das chaves lê do lugar antigo
    return new if new.exists() else None


def convert_json(run: Run, path: Path | None, fn, label: str | None = None) -> None:
    if path is None or not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
        old = json.loads(raw)
    except (OSError, ValueError) as e:
        run.warn(f"{path}: não é um JSON legível ({e.__class__.__name__}); deixado como está")
        return
    new = fn(json.loads(raw))
    if new != old:
        run.say(f"chaves traduzidas: {label or path}")
        if not run.dry:
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remove(run: Run, path: Path, why: str) -> None:
    if path.is_file():
        run.say(f"apagado ({why}): {path}")
        if not run.dry:
            path.unlink()


def migrate_home(run: Run, home: Path) -> None:
    if not home.is_dir():
        run.warn(f"{home} não existe; nada a migrar em $JARVIS_HOME")
        return
    convert_json(run, move(run, home / "assistentes.json", home / "assistants.json"), conv_assistants)
    convert_json(run, move(run, home / "escritorio.json", home / "office.json"), conv_office)
    office = move(run, home / "escritorio", home / "office")
    if office is not None:
        convert_json(run, move(run, office / "cargos.json", office / "positions.json"), conv_position_ids)
        convert_json(run, move(run, office / "cargo.json", office / "position.json"), conv_position_legacy)
        convert_json(run, move(run, office / "rotinas.json", office / "routines.json"), conv_routines)
        convert_json(run, move(run, office / "carteiro.json", office / "courier.json"), conv_courier)
        for cache in ("tokens.json", "tokens.en.json"):
            remove(run, office / cache, "cache dos tokens do Escritório; refeito em um minuto")
    convert_json(run, move(run, home / "pensa.json", home / "thinker.json"), conv_thinker)
    convert_json(run, move(run, home / "pensa.cache.json", home / "thinker.cache.json"), conv_thinker_cache)
    convert_json(run, move(run, home / "pensa.login.json", home / "thinker.login.json"), conv_login)
    move(run, home / "logs" / "pensa-login.log", home / "logs" / "thinker-login.log")
    move(run, home / "logs" / "carteiro.log", home / "logs" / "courier.log")
    decision = move(run, home / "telegram.decisao", home / "telegram.decision")
    if decision is not None and decision.is_file() and decision.read_text(encoding="utf-8").strip() == "nao":
        run.say(f"conteúdo traduzido: {decision} (nao → no)")
        if not run.dry:
            decision.write_text("no\n", encoding="utf-8")
    assistants_dir = move(run, home / "assistentes", home / "assistants")
    if assistants_dir is not None and assistants_dir.is_dir():
        for f in sorted(assistants_dir.glob("*.whatsapp.json")):
            convert_json(run, f, conv_login)
    convert_json(run, home / "update.json", conv_update)
    convert_json(run, home / "update.run.json", conv_update)
    # render/: refeito no próximo bootstrap; renomeado e traduzido já, para o JARVIS Web e o bootstrap não verem nomes antigos
    hermes = home / "render" / "hermes"
    convert_json(run, move(run, hermes / "assistentes.json", hermes / "assistants.json"), conv_assistants)
    move(run, hermes / "assistentes.applied", hermes / "assistants.applied")
    move(run, hermes / "assistentes", hermes / "assistants")
    move(run, hermes / "cargos", hermes / "positions")
    move(run, hermes / "modelo.yaml", hermes / "model.yaml")
    convert_json(run, move(run, home / "render" / "escritorio.json", home / "render" / "office.json"), conv_office)
    convert_json(run, home / "render" / "secrets.json", conv_secrets_catalog)
    convert_json(run, home / "render" / "web.json", conv_web)
    migrate_secrets_env(run, home / "secrets.env")
    migrate_paths(run, home)
    if (home / "office" / "positions.json").is_file() or (home / "escritorio" / "cargos.json").is_file():
        print("[migrate] nota: o cargo padrão `trabalho` agora é `work`; o bootstrap cria o perfil do Hermes `work`. "
              "O perfil antigo pode ser apagado com: hermes profile delete trabalho")


def migrate_secrets_env(run: Run, path: Path) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    old = re.search(r"(?m)^GITHUB_LEITURA_TOKEN=(.*)$", text)
    if not old:
        return
    new = re.search(r"(?m)^GITHUB_READ_TOKEN=(.*)$", text)
    if new and new.group(1).strip() and old.group(1).strip() and new.group(1).strip() != old.group(1).strip():
        run.warn(f"{path}: GITHUB_LEITURA_TOKEN e GITHUB_READ_TOKEN têm valores diferentes; nada foi mexido")
        return
    if new:
        value = new.group(1) if new.group(1).strip() else old.group(1)
        text = re.sub(r"(?m)^GITHUB_READ_TOKEN=.*$", lambda _: "GITHUB_READ_TOKEN=" + value, text)
        text = re.sub(r"(?m)^GITHUB_LEITURA_TOKEN=.*\n?", "", text)
    else:
        text = re.sub(r"(?m)^GITHUB_LEITURA_TOKEN=", "GITHUB_READ_TOKEN=", text)
    run.say(f"segredo renomeado em {path}: GITHUB_LEITURA_TOKEN → GITHUB_READ_TOKEN (o valor fica)")
    if not run.dry:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)


def new_tenant_path(p: str) -> str:
    q = p.replace("/tenants/exemplo/addon-exemplo/", "/tenants/example/addon-example/")
    return q.replace("/tenants/pessoal/", "/tenants/personal/").replace("/tenants/exemplo/", "/tenants/example/")


def migrate_paths(run: Run, home: Path) -> None:
    """tenant.path e addons.json que apontavam para os tenants do kernel com o nome antigo."""
    tp = home / "tenant.path"
    if tp.is_file():
        cur = tp.read_text(encoding="utf-8").strip()
        new = new_tenant_path(cur)
        if new != cur and Path(new).is_file():
            run.say(f"tenant.path: {cur} → {new}")
            if not run.dry:
                tp.write_text(new + "\n", encoding="utf-8")
    ap = home / "addons.json"
    if ap.is_file():
        try:
            d = json.loads(ap.read_text(encoding="utf-8"))
        except ValueError:
            return
        changed = False
        for a in d if isinstance(d, list) else []:
            if isinstance(a, dict) and isinstance(a.get("path"), str):
                new = new_tenant_path(a["path"])
                if new != a["path"] and Path(new).is_file():
                    a["path"], changed = new, True
                    if a.get("addon") == "exemplo-addon":
                        a["addon"] = "example-addon"
                    a["skills"] = ["example-addon-hello" if s == "exemplo-addon-ola" else s for s in a.get("skills") or []]
        if changed:
            run.say("addons.json: caminhos dos add-ons do kernel atualizados")
            if not run.dry:
                ap.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_data(run: Run, data: Path) -> None:
    if not data.is_dir():
        run.warn(f"{data} não existe; nada a migrar em $JARVIS_DATA")
        return
    convert_json(run, move(run, data / "preferencias.json", data / "preferences.json"), conv_preferences)


# ---------------------------------------------------------------- YAML (linha a linha, sem PyYAML, mantendo comentários)
KEY_LINE = re.compile(r"^(?P<ind>[ \t]*)(?P<dash>-[ \t]+)?(?P<key>[A-Za-z_][\w-]*)(?P<colon>[ \t]*:)(?P<rest>(?:[ \t].*)?)$")
ITEM_LINE = re.compile(r"^(?P<ind>[ \t]*)-[ \t]+(?P<rest>[^{\[#:][^:#]*?)(?P<tail>[ \t]*(?:#.*)?)$")
VALUE = re.compile(r"^(?P<lead>[ \t]*)(?P<val>\"[^\"]*\"|'[^']*'|\[[^\]]*\]|\{[^}]*\}|[^#]*?)(?P<tail>[ \t]*(?:#.*)?)$")
USER_KEYS = {("env",), ("headers",), ("export", "groups")}  # chaves que são dado do tenant (variáveis, cabeçalhos, grupos)


def new_key(path: tuple, key: str) -> str:
    if path[-1:] in {("env",), ("headers",)} or path[-2:] == ("export", "groups"):
        return key
    if key == "provedor" and path == ("hermes",):
        return "subscription"  # hermes.provider já é o provedor do Hermes; a assinatura de quem pensa é subscription
    return YAML_KEYS.get(key, key)


def value_map(path: tuple, key: str) -> dict | None:
    """Tabela de valores de enum para a chave `key` (nome novo) dentro de `path` (nomes novos)."""
    if key == "modules" and path == ("interface",):
        return MODULES
    if key == "provider" and path == ("decisions",):
        return DECISION_PROVIDERS
    if key == "type" and path == ("assistants",):
        return ASSISTANT_TYPES
    if key == "type" and path == ("profiles",):
        return PROFILE_TYPES
    if key == "level":
        return LEVELS
    if key in ("role", "roles"):
        return ROLES
    if key == "position" and path == ("office", "routines"):
        return POSITIONS
    if key == "name" and path == ("office", "routines"):
        return ROUTINES
    if key == "name" and path == ("office", "positions"):
        return POSITIONS
    if key == "tenant" and path == ():
        return TENANTS
    if key == "where" and path == ("office", "routines"):
        return {"__where__": ""}
    return None


def map_scalar(v: str, table: dict) -> str:
    q = v[0] if v[:1] in "\"'" and len(v) >= 2 and v[-1] == v[0] else ""
    bare = v[1:-1] if q else v
    if "__where__" in table:
        new = where_value(bare)
    else:
        new = table.get(bare, bare)
    return q + new + q if new != bare else v


def map_value(rest: str, table: dict | None) -> str:
    if not table or not rest.strip():
        return rest
    m = VALUE.match(rest)
    if not m or not m.group("val").strip():
        return rest
    val = m.group("val").rstrip()
    trailing = m.group("val")[len(val):]
    if val.startswith("[") and val.endswith("]"):
        items = [x.strip() for x in val[1:-1].split(",")]
        new = "[" + ", ".join(map_scalar(x, table) if x else x for x in items) + "]"
    elif val.startswith("{"):
        return rest
    else:
        new = map_scalar(val, table)
    return m.group("lead") + new + trailing + m.group("tail") if new != val else rest


def flow_keys(rest: str, path: tuple) -> str:
    """Chaves dentro de um mapa em linha ({host: x, papeis: [a]}): traduz chave e valor."""
    m = VALUE.match(rest)
    if not m or not m.group("val").strip().startswith("{"):
        return rest

    def one(mm):
        k = new_key(path, mm.group(2))
        v = mm.group(4)
        t = value_map(path, k)
        return mm.group(1) + k + mm.group(3) + (map_value(" " + v, t)[1:] if t else v)
    inner = re.sub(r"([{,]\s*)([A-Za-z_][\w-]*)(\s*:\s*)(\[[^\]]*\]|[^,}]*)", one, m.group("val"))
    return m.group("lead") + inner + m.group("tail")


def rewrite_yaml(text: str) -> str:
    out: list[str] = []
    stack: list[tuple[int, str]] = []  # (coluna da chave, nome novo)
    block_col = None  # dentro de um escalar em bloco (| ou >): linhas mais fundas que a chave não são chaves
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        stripped = body.strip()
        indent = len(body) - len(body.lstrip(" \t"))
        if block_col is not None:
            if stripped and indent <= block_col:
                block_col = None
            else:
                out.append(line)
                continue
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        m = KEY_LINE.match(body)
        if m:
            col = len(m.group("ind")) + len(m.group("dash") or "")
            while stack and stack[-1][0] >= col:
                stack.pop()
            path = tuple(k for _, k in stack)
            key = new_key(path, m.group("key"))
            rest = m.group("rest")
            rest = flow_keys(rest, path + (key,)) if rest.strip().startswith("{") else map_value(rest, value_map(path, key))
            stack.append((col, key))
            if re.match(r"^[ \t]*[|>][-+0-9]*[ \t]*(#.*)?$", rest):
                block_col = col
            out.append(m.group("ind") + (m.group("dash") or "") + key + m.group("colon") + rest + nl)
            continue
        m = re.match(r"^(?P<ind>[ \t]*-[ \t]+)(?P<rest>\{.*)$", body)
        if m:  # item de lista que é um mapa em linha (- {host: x, papeis: [a]})
            col = len(m.group("ind"))
            while stack and stack[-1][0] >= col:
                stack.pop()
            out.append(m.group("ind") + flow_keys(m.group("rest"), tuple(k for _, k in stack)) + nl)
            continue
        m = ITEM_LINE.match(body)
        if m:  # item de lista em bloco (- valor): enum da chave dona da lista
            col = len(m.group("ind"))
            while stack and stack[-1][0] > col:
                stack.pop()
            if stack:
                owner = stack[-1][1]
                path = tuple(k for _, k in stack[:-1])
                t = value_map(path, owner)
                if t:
                    val = m.group("rest").rstrip()
                    new = map_scalar(val, t)
                    if new != val:
                        out.append(body[: body.index(val, len(m.group("ind")))] + new + body[body.index(val, len(m.group("ind"))) + len(val):] + nl)
                        continue
        out.append(line)
    return "".join(out)


def migrate_yaml(run: Run, path: Path, label: str) -> None:
    if not path.is_file():
        run.warn(f"{label}: {path} não existe")
        return
    text = path.read_text(encoding="utf-8")
    new = rewrite_yaml(text)
    if new == text:
        print(f"[migrate] {label}: {path} já está com os nomes novos")
        return
    changed = [f"{a.strip()} → {b.strip()}" for a, b in zip(text.splitlines(), new.splitlines()) if a != b]
    bak = path.with_name(path.name + ".bak")
    run.say(f"{label}: {path} reescrito ({len(changed)} linha(s)); cópia em {bak}")
    for c in changed:
        print(f"[migrate]     {c}")
    if not run.dry:
        if not bak.exists():
            shutil.copy2(path, bak)
        else:
            run.warn(f"{bak} já existia; mantido (a cópia é a do primeiro conversor)")
        path.write_text(new, encoding="utf-8")
    if re.search(r"(?m)^tenant:\s*(pessoal|exemplo)\b", text):
        print("[migrate] nota: o tenant mudou de nome; mova a pasta dele no vault (tenants/pessoal/ → tenants/personal/, "
              "tenants/exemplo/ → tenants/example/)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", help="$JARVIS_HOME (ex.: ~/.jarvis)")
    ap.add_argument("--data", help="$JARVIS_DATA (ex.: ~/.whitebeard), para o preferencias.json do JARVIS Web")
    ap.add_argument("--tenant", help="jarvis.yaml a reescrever (guarda uma cópia .bak)")
    ap.add_argument("--addon", action="append", default=[], help="addon.yaml a reescrever (repetível)")
    ap.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    a = ap.parse_args()
    if not (a.home or a.data or a.tenant or a.addon):
        ap.error("diga o que migrar: --home, --data, --tenant e/ou --addon")
    run = Run(a.dry_run)
    if a.home:
        migrate_home(run, Path(a.home).expanduser())
    if a.data:
        migrate_data(run, Path(a.data).expanduser())
    if a.tenant:
        migrate_yaml(run, Path(a.tenant).expanduser(), "jarvis.yaml")
    for f in a.addon:
        migrate_yaml(run, Path(f).expanduser(), "addon.yaml")
    verb = "mudaria" if a.dry_run else "mudou"
    print(f"[migrate] {'nada a fazer: tudo já está com os nomes da 0.4.0' if not run.changes else f'{run.changes} mudança(s) ({verb})'}"
          + (f"; {run.warnings} aviso(s)" if run.warnings else ""))
    if run.changes and not a.dry_run:
        print("[migrate] agora: ./bootstrap.sh --apply e /reset nas conversas dos assistentes")
    return 1 if run.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
