#!/usr/bin/env python3
"""jarvis_setup.py — configuração depois da instalação, sem terminal interativo.

Quem usa: o JARVIS Web (página PRIMEIROS PASSOS) e o bootstrap.sh (--telegram). Só biblioteca padrão,
para rodar com o python3 do sistema a partir de um serviço. Cada comando imprime UMA linha de JSON.

Segredos nunca passam por argumento nem saem na resposta: entram por stdin e ficam em
$JARVIS_HOME/secrets.env (modo 600). O estado diz só se cada um está preenchido.

  jarvis_setup.py --home ~/.jarvis status
  jarvis_setup.py --home ~/.jarvis set-secret NOME            valor em stdin; vazio limpa
  jarvis_setup.py --home ~/.jarvis telegram-token             token em stdin; valida em getMe e guarda
  jarvis_setup.py --home ~/.jarvis telegram-wait [--seconds N] [--since EPOCH]
                                                              espera a primeira mensagem ao bot e guarda o chat
  jarvis_setup.py --home ~/.jarvis telegram-off               a pessoa não quer Telegram (não perguntar de novo)
  jarvis_setup.py --home ~/.jarvis done                       marca os primeiros passos como concluídos
  jarvis_setup.py --home ~/.jarvis update-check [--max-age S] [--force]
                                                              há versão nova do kernel/JARVIS Web no GitHub?
                                                              (resposta da rede guardada em update.json por S segundos)
  jarvis_setup.py --home ~/.jarvis update-run                 roda o install.sh desacoplado (sobrevive ao reinício
                                                              do JARVIS Web); log em logs/update.log
  jarvis_setup.py --home ~/.jarvis update-status              andamento/resultado da atualização em curso ou da última
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from jarvis_i18n import T  # mesmo diretório (o do script está em sys.path)

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# o kernel gera ou deriva estes; o Telegram tem o seu próprio passo
HIDDEN = {"GBRAIN_MCP_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"}


def out(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0 if obj.get("ok", True) else 1


def tv(value):
    """T() para texto que vem de dado (arquivo do kernel, tenant, outro módulo): o padrão do kernel que tem tradução
    sai no idioma da interface; o que a pessoa escreveu passa igual. Só na resposta, nunca no que é gravado."""
    return T(value) if isinstance(value, str) and value else value


# Textos dos segredos de cada assistente: o render os grava com o nome do assistente dentro (render/secrets.json)
SECRET_TEMPLATES = [
    (re.compile(r"^Token do bot do assistente (?P<name>\S+)$"), lambda m: T("Token do bot do assistente {name}", name=m["name"])),
    (re.compile(r"^Token do bot do assistente (?P<name>\S+) \((?P<channel>[^()]+)\)$"),
     lambda m: T("Token do bot do assistente {name} ({channel})", name=m["name"], channel=m["channel"])),
    (re.compile(r"^Quem fala com o assistente (?P<name>\S+)$"), lambda m: T("Quem fala com o assistente {name}", name=m["name"])),
    (re.compile(r"^Grupos do assistente (?P<name>\S+)$"), lambda m: T("Grupos do assistente {name}", name=m["name"])),
    (re.compile(r"^Canais do assistente (?P<name>\S+)$"), lambda m: T("Canais do assistente {name}", name=m["name"])),
    (re.compile(r"^Token do bot deste assistente no (?P<channel>\S+) \((?P<label>.*)\)\. Um bot, um assistente\.$", re.S),
     lambda m: T("Token do bot deste assistente no {channel} ({label}). Um bot, um assistente.", channel=m["channel"], label=tv(m["label"]))),
]


def secret_text(value: str) -> str:
    """Título, descrição ou "como obter" de um segredo, no idioma da interface."""
    if not value:
        return value
    for template, phrase in SECRET_TEMPLATES:
        m = template.match(value)
        if m:
            return phrase(m)
    return T(value)


def read_env(path: Path) -> dict[str, str]:
    """NOME=valor de um arquivo gerado pelo kernel (valores entre aspas simples ou nus)."""
    vals: dict[str, str] = {}
    if not path.is_file():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            parts = shlex.split(v)
        except ValueError:
            parts = [v]
        vals[k.strip()] = parts[0] if parts else ""
    return vals


def set_secret(path: Path, name: str, value: str) -> None:
    """Mesma regra do bootstrap: substitui a linha NOME=..., ou acrescenta; valor entre aspas simples."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    quoted = ("'" + value.replace("'", "'\\''") + "'") if value else ""
    new_line = f"{name}={quoted}"
    text2, n = re.subn(r"(?m)^" + re.escape(name) + r"=.*$", lambda _: new_line, text)
    if not n:
        text2 = text + ("" if text.endswith("\n") or not text else "\n") + new_line + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text2, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def stdin_value() -> str:
    data = sys.stdin.read()
    # uma linha: o \n final vem do terminal ou do echo, não é parte do valor
    if data.endswith("\n"):
        data = data[:-1]
    if data.endswith("\r"):
        data = data[:-1]
    return data.strip()


def declared_secrets(home: Path) -> list[dict]:
    try:
        return json.loads((home / "render" / "secrets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- status
def pending_apply(home: Path) -> bool:
    """Há algo gravado (segredo ou assistente) e ainda não colocado em uso: secrets.env ou assistants.json
    mais novos que a marca setup.applied (que o bootstrap toca ao aplicar)."""
    try:
        applied = (home / "setup.applied").stat().st_mtime
    except OSError:
        return False
    for f in (home / "secrets.env", store_path(home), home / "office.json", home / "thinker.json"):
        try:
            if f.stat().st_mtime > applied + 1:
                return True
        except OSError:
            pass
    return False


ASSISTANT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
ASSISTANT_TYPES = ("owner", "team", "client", "routines")
# Onde o assistente fala e os nomes dos segredos dele (espelho de CANAIS no jarvis_render.py)
ASSISTANT_CHANNELS = {
    "telegram": {"token": "TELEGRAM_BOT_TOKEN", "people": "TELEGRAM_ALLOWED_USERS", "groups": "TELEGRAM_GROUP_ALLOWED_CHATS"},
    "discord": {"token": "DISCORD_BOT_TOKEN", "people": "DISCORD_ALLOWED_USERS", "groups": "DISCORD_ALLOWED_CHANNELS"},
    "whatsapp": {"token": None, "people": "WHATSAPP_ALLOWED_USERS", "groups": "WHATSAPP_GROUP_ALLOWED_USERS"},
}
WHATSAPP_MODES = ("bot", "self-chat")


def channel_of(a: dict) -> str:
    c = a.get("channel") or "telegram"
    return c if c in ASSISTANT_CHANNELS else "telegram"


def assistant_keys(name: str, channel: str) -> dict[str, str]:
    c = ASSISTANT_CHANNELS.get(channel) or ASSISTANT_CHANNELS["telegram"]
    return {k: (f"{v}_{suffix(name)}" if v else "") for k, v in c.items()}


def hermes_home_of(home: Path) -> Path:
    return Path(read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes"))


def whatsapp_session(home: Path, name: str) -> Path:
    return home / "assistants" / name / "whatsapp" / "session"


def whatsapp_state_path(home: Path, name: str) -> Path:
    return home / "assistants" / f"{name}.whatsapp.json"


def whatsapp_phone(session: Path) -> str:
    try:
        me = json.loads((session / "creds.json").read_text(encoding="utf-8")).get("me") or {}
        return str(me.get("id", "")).split(":", 1)[0].split("@", 1)[0]
    except (OSError, ValueError, AttributeError):
        return ""


def whatsapp_bridge_dir(home: Path) -> Path:
    return hermes_home_of(home) / "hermes-agent" / "scripts" / "whatsapp-bridge"


def whatsapp_bridge_ready(home: Path) -> str | None:
    """Garante a ponte (Node) do Hermes com as dependências instaladas. Devolve o erro, ou None."""
    bridge = whatsapp_bridge_dir(home)
    if not (bridge / "bridge.js").is_file():
        return T("a ponte do WhatsApp do Hermes não está instalada neste computador")
    if (bridge / "node_modules").is_dir():
        return None
    npm = shutil.which("npm")
    if not npm or not shutil.which("node"):
        return T("node/npm não encontrados; o kernel fixa o Node na instalação (rode o instalador de novo)")
    try:
        r = subprocess.run([npm, "install", "--omit=dev", "--no-audit", "--no-fund", "--silent"], cwd=str(bridge), capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return T("não consegui preparar a ponte do WhatsApp ({error})", error=e.__class__.__name__)
    if r.returncode != 0:
        return T("não consegui preparar a ponte do WhatsApp (npm install falhou; veja a internet e tente de novo)")
    return None


def whatsapp_live_pairing(home: Path, name: str) -> dict | None:
    """Estado do pareamento em curso (arquivo escrito por jarvis_whatsapp_pair.py), ou None se não há."""
    try:
        d = json.loads(whatsapp_state_path(home, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = d.get("pid")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if d.get("state") == "cancelled":
        return None
    if d.get("state") in ("preparing", "waiting", "qr") and not alive:
        d["state"] = "error" if d.get("state") != "qr" else "expired"
        d["error"] = d.get("error") or T("o pareamento parou; gere outro QR")
    return d


def whatsapp_stop_pairing(home: Path, name: str) -> None:
    d = whatsapp_live_pairing(home, name) or {}
    pid = d.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 15)
            for _ in range(20):  # o processo escreve "cancelado" e sai; só então o arquivo pode ir
                time.sleep(0.25)
                os.kill(pid, 0)
        except OSError:
            pass
    whatsapp_state_path(home, name).unlink(missing_ok=True)


def forget_assistant_secrets(home: Path, name: str) -> None:
    """Esquece token e listas do assistente em todos os canais (revoga o acesso); a sessão do WhatsApp também."""
    env = read_env(home / "secrets.env")
    for channel in ASSISTANT_CHANNELS:
        for k in assistant_keys(name, channel).values():
            if k and k in env:
                set_secret(home / "secrets.env", k, "")
    whatsapp_stop_pairing(home, name)
    shutil.rmtree(whatsapp_session(home, name), ignore_errors=True)


def store_path(home: Path) -> Path:
    return home / "assistants.json"


def load_store(home: Path) -> list[dict]:
    """Assistentes criados pelo JARVIS Web (fora do jarvis.yaml, que pode ser o do kernel ou de um pacote)."""
    try:
        data = json.loads(store_path(home).read_text(encoding="utf-8"))
        return [a for a in data if isinstance(a, dict) and a.get("name")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_store(home: Path, items: list[dict]) -> None:
    store_path(home).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def suffix(name: str) -> str:
    return name.upper().replace("-", "_")


def live_names(home: Path, names: object, key: str) -> list[str]:
    """Nomes das pessoas/grupos reconhecidos cujo id ainda está na lista do secrets.env (ordem da lista)."""
    ids = [x for x in read_env(home / "secrets.env").get(key, "").split(",") if x]
    mapping = names if isinstance(names, dict) else {}
    return [mapping.get(i) or T("(sem nome)") for i in ids]


def live_list(home: Path, names: object, key: str) -> list[dict]:
    """Como nomes_vivos, com o id: é por ele que a página tira alguém da lista."""
    ids = [x for x in read_env(home / "secrets.env").get(key, "").split(",") if x]
    mapping = names if isinstance(names, dict) else {}
    return [{"id": i, "name": mapping.get(i) or T("(sem nome)")} for i in ids]


def hermes_profile_dir(home: Path, name: str) -> Path:
    hermes_home = read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(hermes_home) / "profiles" / name


def clear_stale_waits(home: Path) -> None:
    """Perfil estacionado por uma espera que a página abandonou (PARAR, aba fechada): volta depois de 5 min."""
    base = hermes_profile_dir(home, "x").parent
    if not base.is_dir():
        return
    for marker in base.glob("*/.whitebeard-wait"):
        try:
            if time.time() - int(marker.read_text(encoding="utf-8").strip() or "0") > 300:
                marker.unlink(missing_ok=True)
                (marker.parent / "gateway.parked").unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def assistants_status(home: Path, filled: dict[str, bool]) -> list[dict]:
    """Assistentes do Telegram: os criados pelo JARVIS Web (loja) e os do tenant (catálogo do render), com o que
    falta a cada um. `aplicado`: já virou perfil do Hermes (está no catálogo renderizado)."""
    try:
        cat = json.loads((home / "render" / "hermes" / "assistants.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cat = []
    by_name = {a.get("name"): a for a in cat if isinstance(a, dict)}
    store = load_store(home)
    removed = {s.get("name") for s in store if s.get("removed")}
    store = [s for s in store if not s.get("removed")]
    store_names = {s.get("name") for s in store}
    out_: list[dict] = []
    seen: set[str] = set()
    for a in store + [c for c in cat if c.get("name") not in store_names]:
        name = a.get("name", "")
        if not name or name in seen or name in removed:
            continue
        seen.add(name)
        kind = a.get("type", "")
        channel = channel_of(a)
        ch = assistant_keys(name, channel)
        # a loja é o que a pessoa decidiu (edição ainda não aplicada inclusive); o catálogo só completa
        cat_a = by_name.get(name) or {}
        wa = None
        if channel == "whatsapp":
            sess = whatsapp_session(home, name)
            pairing = whatsapp_live_pairing(home, name) or {}
            wa = {"mode": a.get("whatsapp_mode") or "bot", "paired": (sess / "creds.json").is_file(), "phone": whatsapp_phone(sess),
                  "pairing": pairing.get("state") if pairing.get("state") in ("preparing", "waiting", "qr") else None}
        out_.append({
            "name": name, "type": kind, "channel": channel, "description": tv(a.get("description", "")), "bot": a.get("bot", "") or cat_a.get("bot", ""),
            "app_id": a.get("app_id", ""), "whatsapp": wa,
            "skills": a.get("skills") if a.get("skills") is not None else (cat_a.get("skills") or []),
            "settings": {"computer": bool((a.get("settings") or {}).get("computer")), "write_notes": bool((a.get("settings") or {}).get("write_notes"))},
            "level": a.get("level") or "",
            "provider": a.get("provider") or "",
            "origin": "web" if name in store_names else "tenant",
            "from_pack": bool(a.get("from_pack")) or (name not in store_names),
            "token": bool(wa["paired"]) if wa else bool(filled.get(ch["token"], False)),
            "people": bool(filled.get(ch["people"], False)) if kind in ("owner", "team") else None,
            "groups": bool(filled.get(ch["groups"], False)) if kind in ("team", "client") else None,
            "applied": name in by_name and (cat_a.get("channel") or "telegram") == channel,
            "people_names": live_names(home, a.get("people_names"), ch["people"]),
            "group_names": live_names(home, a.get("group_names"), ch["groups"]),
            "people_list": live_list(home, a.get("people_names"), ch["people"]),
            "group_list": live_list(home, a.get("group_names"), ch["groups"]),
            "secrets": {"token": ch["token"] or None,
                         "people": ch["people"] if kind in ("owner", "team") else None,
                         "groups": ch["groups"] if kind in ("team", "client") else None},
        })
    return out_


# ---------------------------------------------------------------- assistentes (criar, token, reconhecer, remover)
def cmd_assistant_add(home: Path) -> int:
    raw = stdin_value()
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "error": T("dados inválidos")})
    name = str(payload.get("name") or "").strip().lower()
    kind = str(payload.get("type") or "").strip()
    channel = str(payload.get("channel") or "telegram").strip()
    description = str(payload.get("description") or "").strip()[:400]
    if not ASSISTANT_NAME_RE.match(name):
        return out({"ok": False, "error": T("nome: só letras minúsculas, números e hífen, começando por letra (2 a 31 caracteres)")})
    if kind not in ASSISTANT_TYPES:
        return out({"ok": False, "error": T("tipo desconhecido")})
    if channel not in ASSISTANT_CHANNELS:
        return out({"ok": False, "error": T("canal desconhecido")})
    mode = str(payload.get("whatsapp_mode") or "bot")
    if channel == "whatsapp" and mode not in WHATSAPP_MODES:
        return out({"ok": False, "error": T("modo do WhatsApp desconhecido")})
    if name == "default":
        return out({"ok": False, "error": T("esse nome é reservado")})
    items = load_store(home)
    if any(a.get("name") == name and not a.get("removed") for a in items) or (
        (home / "render" / "hermes" / "assistants" / name).is_dir() and not any(a.get("name") == name and a.get("removed") for a in items)
    ):
        return out({"ok": False, "error": T("já existe um assistente chamado {name}", name=name)})
    items = [a for a in items if a.get("name") != name]
    items.append({"name": name, "type": kind, "channel": channel, "description": description, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  **({"whatsapp_mode": mode} if channel == "whatsapp" else {})})
    save_store(home, items)
    return out({"ok": True, "assistant": {"name": name, "type": kind, "channel": channel, "description": description}})


def in_catalog(home: Path, name: str) -> dict | None:
    try:
        for a in json.loads((home / "render" / "hermes" / "assistants.json").read_text(encoding="utf-8")):
            if a.get("name") == name:
                return a
    except (OSError, ValueError):
        pass
    return None


def cmd_assistant_remove(home: Path, name: str) -> int:
    items = load_store(home)
    in_store = any(a.get("name") == name and not a.get("removed") for a in items)
    from_pack = (in_catalog(home, name) or {}).get("from_pack") or (not in_store and in_catalog(home, name) is not None)
    if not in_store and not from_pack:
        return out({"ok": False, "error": T("assistente desconhecido")})
    items = [a for a in items if a.get("name") != name]
    if from_pack:
        # o pacote continua a declará-lo; a marca faz o render ignorá-lo (e o bootstrap estaciona o perfil)
        items.append({"name": name, "removed": True})
    save_store(home, items)
    # revoga o acesso: token e listas somem deste computador
    forget_assistant_secrets(home, name)
    return out({"ok": True})


def cmd_assistant_forget(home: Path, name: str, which: str, ident: str) -> int:
    """Tira uma pessoa (ou um grupo/canal) da lista de quem fala com o assistente. Vale depois de colocar em uso."""
    a = assistant_from_store(home, name)
    if not a:
        return out({"ok": False, "error": T("assistente desconhecido")})
    if which not in ("person", "group"):
        return out({"ok": False, "error": T("diga se é pessoa ou grupo")})
    key = assistant_keys(name, channel_of(a))["people" if which == "person" else "groups"]
    current = [x for x in read_env(home / "secrets.env").get(key, "").split(",") if x]
    if ident not in current:
        return out({"ok": False, "error": T("essa pessoa não está na lista") if which == "person" else T("esse grupo não está na lista")})
    set_secret(home / "secrets.env", key, ",".join(x for x in current if x != ident))
    field = "people_names" if which == "person" else "group_names"
    items = load_store(home)
    label = ""
    for it in items:
        if it.get("name") == name and isinstance(it.get(field), dict):
            label = str(it[field].pop(ident, "") or "")
    save_store(home, items)
    return out({"ok": True, "name": label})


def cmd_assistant_update(home: Path, name: str) -> int:
    """Edita descrição, tipo e ajustes. Um assistente do pacote passa a ter uma cópia na loja, que vale por cima."""
    raw = stdin_value()
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "error": T("dados inválidos")})
    items = load_store(home)
    current = next((a for a in items if a.get("name") == name and not a.get("removed")), None)
    if current is None:
        base = in_catalog(home, name)
        if base is None:
            return out({"ok": False, "error": T("assistente desconhecido")})
        current = {"name": name, "type": base.get("type", ""), "channel": base.get("channel") or "telegram", "description": base.get("description", ""),
                 "skills": base.get("skills") or [], "settings": base.get("settings") or {}, "from_pack": True}
        items.append(current)
    if "channel" in payload:
        channel = str(payload.get("channel") or "telegram")
        if channel not in ASSISTANT_CHANNELS:
            return out({"ok": False, "error": T("canal desconhecido")})
        if channel != channel_of(current):
            # trocar de canal é recomeçar: o bot, as pessoas e os grupos do canal antigo não valem no novo
            forget_assistant_secrets(home, name)
            current["channel"] = channel
            for k in ("bot", "app_id", "people_names", "group_names"):
                current.pop(k, None)
    if "whatsapp_mode" in payload and channel_of(current) == "whatsapp":
        mode = str(payload.get("whatsapp_mode") or "bot")
        if mode not in WHATSAPP_MODES:
            return out({"ok": False, "error": T("modo do WhatsApp desconhecido")})
        if mode != (current.get("whatsapp_mode") or "bot"):
            # trocar o modo é parear de novo (outro número)
            forget_assistant_secrets(home, name)
            current.pop("people_names", None); current.pop("group_names", None)
        current["whatsapp_mode"] = mode
    if "description" in payload:
        current["description"] = str(payload.get("description") or "").strip()[:400]
    if "type" in payload:
        kind = str(payload.get("type") or "")
        if kind not in ASSISTANT_TYPES:
            return out({"ok": False, "error": T("tipo desconhecido")})
        if kind != current.get("type"):
            current["type"] = kind
            current.pop("skills", None)  # skills padrão do tipo novo
            current["settings"] = {}
    if "settings" in payload:
        aj = payload.get("settings") or {}
        if current.get("type") in ("client", "routines") and any(aj.values()):
            return out({"ok": False, "error": T("este tipo não aceita ajustes")})
        current["settings"] = {k: bool(aj.get(k)) for k in ("computer", "write_notes") if k in aj}
    if "skills" in payload and isinstance(payload.get("skills"), list):
        current["skills"] = [str(s) for s in payload["skills"] if re.match(r"^[a-z][a-z0-9-]*$", str(s))]
    if "level" in payload:
        lv = str(payload.get("level") or "")
        if lv and lv not in ("fast", "balanced", "capable"):
            return out({"ok": False, "error": T("nível desconhecido")})
        if lv:
            current["level"] = lv
        else:
            current.pop("level", None)  # vazio: segue o nível geral
    if "provider" in payload:
        prov = str(payload.get("provider") or "")
        if prov and prov not in ("claude", "chatgpt", "openrouter"):
            return out({"ok": False, "error": T("assinatura desconhecida")})
        if prov:
            current["provider"] = prov
        else:
            current.pop("provider", None)  # vazio: segue a assinatura geral
    save_store(home, items)
    return out({"ok": True, "assistant": {k: current.get(k) for k in ("name", "type", "channel", "whatsapp_mode", "description", "settings", "level", "provider")}})


def assistant_from_store(home: Path, name: str) -> dict | None:
    for a in load_store(home):
        if a.get("name") == name:
            return None if a.get("removed") else a
    try:
        for a in json.loads((home / "render" / "hermes" / "assistants.json").read_text(encoding="utf-8")):
            if a.get("name") == name:
                return a
    except (OSError, ValueError):
        pass
    return None


def cmd_assistant_token(home: Path, name: str) -> int:
    a = assistant_from_store(home, name)
    if not a:
        return out({"ok": False, "error": T("assistente desconhecido")})
    token = stdin_value()
    if not token:
        return out({"ok": False, "error": T("token vazio")})
    channel = channel_of(a)
    ch = assistant_keys(name, channel)
    app_id = ""
    if channel == "whatsapp":
        return out({"ok": False, "error": T("o WhatsApp não usa token: leia o QR na página")})
    if channel == "discord":
        try:
            me = discord_api(token, "/users/@me")
            username = str(me.get("username") or "")
            app_id = str((discord_api(token, "/oauth2/applications/@me") or {}).get("id") or "")
        except DiscordError as e:
            if e.code in (401, 403):
                return out({"ok": False, "error": T("o Discord recusou o token (confira em Developer Portal → Bot → Reset Token)")})
            return out({"ok": False, "error": T("não consegui falar com o Discord; confira a internet e tente de novo")})
        where = T("crie outra aplicação no Developer Portal")
    else:
        try:
            me = tg_api(token, "getMe", http_timeout=20)
        except TelegramError as e:
            if e.code in (401, 404):
                return out({"ok": False, "error": T("o Telegram recusou o token (confira em @BotFather)")})
            return out({"ok": False, "error": T("não consegui falar com o Telegram; confira a internet e tente de novo")})
        username = str(me.get("username") or "")
        where = T("crie outro no @BotFather")
    # o mesmo bot não pode ser dois assistentes (nem o do dono)
    prefix = ASSISTANT_CHANNELS[channel]["token"]
    for k, v in read_env(home / "secrets.env").items():
        if k.startswith(prefix) and k != ch["token"] and v == token:
            return out({"ok": False, "error": T("esse bot já é usado por outro assistente; {where}", where=where)})
    set_secret(home / "secrets.env", ch["token"], token)
    items = load_store(home)
    for it in items:
        if it.get("name") == name:
            it["bot"] = username
            if app_id:
                it["app_id"] = app_id
    save_store(home, items)
    return out({"ok": True, "bot": username, "app_id": app_id, "channel": channel})


def list_add(home: Path, key: str, value: str) -> None:
    current = [x for x in read_env(home / "secrets.env").get(key, "").split(",") if x]
    if value not in current:
        current.append(value)
    set_secret(home / "secrets.env", key, ",".join(current))


class DiscordError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(msg)
        self.code = code


def discord_api(token: str, path: str) -> dict:
    """GET na API do Discord com o token do bot; `code` é o HTTP em caso de recusa."""
    conn = TelegramConnection("discord.com", timeout=20)
    try:
        conn.request("GET", "/api/v10" + path, headers={"Authorization": f"Bot {token}", "User-Agent": "whitebeard-os (jarvis_setup)"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise DiscordError(resp.status, f"HTTP {resp.status}")
        return json.loads(body)
    except DiscordError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise DiscordError(None, str(e)) from e
    finally:
        conn.close()


def hermes_python(home: Path) -> Path | None:
    """O Python do venv do Hermes (tem websockets/aiohttp): é com ele que a espera do Discord roda."""
    hermes_home = read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes")
    p = Path(hermes_home) / "hermes-agent" / "venv" / "bin" / "python"
    return p if p.is_file() else None


def store_name(home: Path, name: str, field: str, key: str, label: str) -> None:
    """Guarda o nome (não o id) de quem foi reconhecido, para a página mostrar; a loja é a dona."""
    items = load_store(home)
    for it in items:
        if it.get("name") == name:
            names = it.setdefault(field, {})
            if isinstance(names, dict):
                names[key] = label
    save_store(home, items)


def assistant_wait_discord(home: Path, name: str, kind: str, token: str, seconds: int, since: int,
                            accepts_person: bool, accepts_group: bool) -> int:
    """Discord: conversa direta reconhece a pessoa; menção num canal reconhece o canal (e quem mencionou, para
    a equipe). O gateway do Hermes pode continuar ligado: o Discord aceita mais de uma sessão do mesmo bot."""
    py = hermes_python(home)
    if not py:
        return out({"ok": False, "error": T("o Hermes não está instalado neste computador")})
    ch = assistant_keys(name, "discord")
    script = Path(__file__).resolve().parent / "jarvis_discord_wait.py"
    env = dict(os.environ, DISCORD_BOT_TOKEN=token)
    try:
        r = subprocess.run([str(py), str(script), str(seconds), str(since)], capture_output=True, text=True, timeout=seconds + 20, env=env, check=False)
        res = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        res = {}
    if not res:
        return out({"ok": False, "error": T("não consegui falar com o Discord; tente de novo")})
    if not res.get("ok"):
        return out({**res, "error": tv(res.get("error"))} if "error" in res else res)
    recognized = []
    for it in res.get("recognized") or [res]:
        if it.get("group_id") and accepts_group:
            list_add(home, ch["groups"], str(it["group_id"]))
            store_name(home, name, "group_names", str(it["group_id"]), str(it.get("group") or ""))
            if accepts_person and it.get("person_id"):
                list_add(home, ch["people"], str(it["person_id"]))
                store_name(home, name, "people_names", str(it["person_id"]), str(it.get("person") or ""))
            recognized.append({"group": it.get("group", ""), "person": it.get("person", "") if accepts_person else ""})
        elif not it.get("group_id") and accepts_person and it.get("person_id"):
            list_add(home, ch["people"], str(it["person_id"]))
            store_name(home, name, "people_names", str(it["person_id"]), str(it.get("person") or ""))
            recognized.append({"person": it.get("person", "")})
        # o que o tipo não aceita (cliente em conversa direta; mim num canal) é ignorado
    if recognized:
        return out({"ok": True, "recognized": recognized, **recognized[0]})
    return out({"ok": False, "error": T("nenhuma mensagem chegou ao bot"), "timed_out": True})


def park_profile(home: Path, name: str) -> None:
    """Marca o perfil como estacionado enquanto a página usa a sessão (o gateway solta a ponte em segundos)."""
    profile = hermes_profile_dir(home, name)
    if profile.is_dir() and not (profile / "gateway.parked").exists():
        (profile / "gateway.parked").write_text("espera do Whitebeard OS\n", encoding="utf-8")
        (profile / ".whitebeard-wait").write_text(str(int(time.time())) + "\n", encoding="utf-8")
        time.sleep(4)


def unpark_profile(home: Path, name: str) -> None:
    profile = hermes_profile_dir(home, name)
    if (profile / ".whitebeard-wait").exists():
        (profile / ".whitebeard-wait").unlink(missing_ok=True)
        (profile / "gateway.parked").unlink(missing_ok=True)


def cmd_assistant_whatsapp_pair(home: Path, name: str) -> int:
    """Começa o pareamento por QR em segundo plano; a página acompanha por whatsapp-status."""
    a = assistant_from_store(home, name)
    if not a or channel_of(a) != "whatsapp":
        return out({"ok": False, "error": T("assistente desconhecido ou não é do WhatsApp")})
    raw = stdin_value()
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    mode = str(payload.get("mode") or a.get("whatsapp_mode") or "bot")
    if mode not in WHATSAPP_MODES:
        return out({"ok": False, "error": T("modo do WhatsApp desconhecido")})
    error = whatsapp_bridge_ready(home)
    if error:
        return out({"ok": False, "error": error})
    whatsapp_stop_pairing(home, name)
    sess = whatsapp_session(home, name)
    # parear de novo = sessão nova (o aparelho antigo some do WhatsApp sozinho ao ser substituído)
    shutil.rmtree(sess, ignore_errors=True)
    park_profile(home, name)
    items = load_store(home)
    for it in items:
        if it.get("name") == name:
            it["whatsapp_mode"] = mode
    save_store(home, items)
    script = Path(__file__).resolve().parent / "jarvis_whatsapp_pair.py"
    state = whatsapp_state_path(home, name)
    state.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([sys.executable, str(script), "--bridge", str(whatsapp_bridge_dir(home)), "--session", str(sess), "--state", str(state), "--mode", mode],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        unpark_profile(home, name)
        return out({"ok": False, "error": T("não consegui iniciar o pareamento ({error})", error=e.__class__.__name__)})
    return out({"ok": True, "state": "preparing"})


def cmd_assistant_whatsapp_status(home: Path, name: str) -> int:
    a = assistant_from_store(home, name)
    if not a or channel_of(a) != "whatsapp":
        return out({"ok": False, "error": T("assistente desconhecido ou não é do WhatsApp")})
    sess = whatsapp_session(home, name)
    pairing = whatsapp_live_pairing(home, name) or {}
    paired = (sess / "creds.json").is_file() and pairing.get("state") in (None, "connected")
    if pairing.get("state") in ("connected", "error", "expired"):
        unpark_profile(home, name)
    return out({"ok": True, "paired": paired, "phone": whatsapp_phone(sess) if paired else "", "name": pairing.get("name", ""),
                "state": pairing.get("state") or ("connected" if paired else "none"), "qr": pairing.get("qr") if pairing.get("state") == "qr" else None,
                "error": tv(pairing.get("error")), "mode": a.get("whatsapp_mode") or "bot"})


def cmd_assistant_whatsapp_unpair(home: Path, name: str) -> int:
    a = assistant_from_store(home, name)
    if not a or channel_of(a) != "whatsapp":
        return out({"ok": False, "error": T("assistente desconhecido ou não é do WhatsApp")})
    whatsapp_stop_pairing(home, name)
    shutil.rmtree(whatsapp_session(home, name), ignore_errors=True)
    unpark_profile(home, name)
    return out({"ok": True})


def assistant_wait_whatsapp(home: Path, name: str, a: dict, seconds: int, since: int, accepts_person: bool, accepts_group: bool) -> int:
    """WhatsApp: sobe a ponte com a sessão do assistente numa porta de espera e lê as mensagens que chegam:
    conversa direta reconhece o número; mensagem num grupo reconhece o grupo. O perfil fica estacionado enquanto
    isso (a mesma sessão não pode estar em duas pontes)."""
    error = whatsapp_bridge_ready(home)
    if error:
        return out({"ok": False, "error": error})
    ch = assistant_keys(name, "whatsapp")
    mode = a.get("whatsapp_mode") or "bot"
    sess = whatsapp_session(home, name)
    node = shutil.which("node")
    port = 3998
    park_profile(home, name)
    env = dict(os.environ, WHATSAPP_MODE=mode, WHATSAPP_DM_POLICY="pairing", WHATSAPP_GROUP_POLICY="open")
    try:
        proc = subprocess.Popen([node, str(whatsapp_bridge_dir(home) / "bridge.js"), "--session", str(sess), "--mode", mode, "--port", str(port)],
                                cwd=str(whatsapp_bridge_dir(home)), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        unpark_profile(home, name)
        return out({"ok": False, "error": T("não consegui subir a ponte do WhatsApp ({error})", error=e.__class__.__name__)})

    def bridge_get(path: str):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
            r = conn.getresponse()
            return json.loads(r.read()) if r.status == 200 else None
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            conn.close()

    found: list[dict] = []
    deadline = time.time() + max(5, min(seconds, 300))
    try:
        while time.time() < deadline and proc.poll() is None:
            msgs = bridge_get("/messages")
            for m in msgs or []:
                chat = str(m.get("chatId") or "")
                who = str(m.get("senderId") or chat)
                if m.get("isGroup") or chat.endswith("@g.us"):
                    if not accepts_group:
                        continue
                    label = chat.split("@", 1)[0]
                    meta = bridge_get(f"/chat/{chat}")
                    if isinstance(meta, dict) and meta.get("name"):
                        label = str(meta["name"])
                    list_add(home, ch["groups"], chat)
                    store_name(home, name, "group_names", chat, label)
                    item = {"group": label}
                elif accepts_person and who:
                    number = who.split(":", 1)[0].split("@", 1)[0]
                    if not number.isdigit():
                        continue
                    list_add(home, ch["people"], number)
                    store_name(home, name, "people_names", number, f"+{number}")
                    item = {"person": f"+{number}"}
                else:
                    continue
                if item not in found:
                    found.append(item)
            # várias pessoas podem mandar na mesma fatia: junta o lote inteiro e devolve logo depois
            if found:
                break
            time.sleep(2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        # a página segue esperando até PARAR ou 3 min: o perfil só volta quando a espera inteira acaba (ou no
        # próximo colocar em uso), para nenhuma mensagem cair no gateway entre duas fatias
        if time.time() - since >= 170:
            unpark_profile(home, name)
    if found:
        return out({"ok": True, "recognized": found, **found[0]})
    return out({"ok": False, "error": T("nenhuma mensagem chegou"), "timed_out": True})


def cmd_assistant_wait(home: Path, name: str, seconds: int, since: int) -> int:
    """Uma fatia da espera da página: cada mensagem ao bot deste assistente reconhece quem mandou (conversa
    direta → "quem fala com ele") ou o grupo (mensagem no grupo, ou o bot adicionado a ele → "grupo dele").
    Junta tudo o que chegar na fatia (várias pessoas de uma vez, em `reconhecidos`); a página chama de novo até
    PARAR ou 3 min. O que o tipo não aceita é ignorado (cliente não ganha pessoas; mim não ganha grupos)."""
    a = assistant_from_store(home, name)
    if not a:
        return out({"ok": False, "error": T("assistente desconhecido")})
    kind = a.get("type", "")
    channel = channel_of(a)
    ch = assistant_keys(name, channel)
    token = read_env(home / "secrets.env").get(ch["token"], "") if ch["token"] else ("paired" if (whatsapp_session(home, name) / "creds.json").is_file() else "")
    if not token:
        return out({"ok": False, "error": T("sem token do bot deste assistente") if ch["token"] else T("o WhatsApp deste assistente ainda não foi pareado")})
    accepts_person = kind in ("owner", "team")
    accepts_group = kind in ("team", "client")
    seconds = max(5, min(seconds, 300))
    if channel == "discord":
        return assistant_wait_discord(home, name, kind, token, seconds, since, accepts_person, accepts_group)
    if channel == "whatsapp":
        return assistant_wait_whatsapp(home, name, a, seconds, since, accepts_person, accepts_group)
    up = suffix(name)
    deadline = time.time() + seconds
    offset = 0
    stopped_gateway = False
    # Se o gateway já serve este bot (assistente aplicado), ele consome as mensagens antes de nós e descarta as
    # de grupos ainda não permitidos: estaciona só este perfil (gateway.parked) enquanto a espera dura. O
    # perfil volta quando algo é reconhecido, quando a espera toda (desde `since`) termina, ou no próximo
    # colocar em uso. A página espera em fatias; a marca .whitebeard-wait lembra que fomos nós.
    profile = hermes_profile_dir(home, name)
    parked = profile / "gateway.parked"
    wait_marker = profile / ".whitebeard-wait"
    if profile.is_dir() and read_env(profile / ".env").get("TELEGRAM_BOT_TOKEN") == token:
        if not parked.exists():
            parked.write_text("espera do JARVIS Web\n", encoding="utf-8")
            wait_marker.write_text(str(int(time.time())) + "\n", encoding="utf-8")
            # o gateway solta o bot em alguns segundos; espera até o Telegram parar de dar 409
            for _ in range(15):
                try:
                    tg_api(token, "getUpdates", offset=-1, timeout=0)
                    break
                except TelegramError as e:
                    if e.code != 409:
                        break
                    time.sleep(2)

    def unpark() -> None:
        if wait_marker.exists():
            wait_marker.unlink(missing_ok=True)
            parked.unlink(missing_ok=True)

    def remember(field: str, key: str, label: str) -> None:
        """Guarda o nome (não o id) de quem foi reconhecido, para a página mostrar; a loja é a dona."""
        items = load_store(home)
        for it in items:
            if it.get("name") == name:
                names = it.setdefault(field, {})
                if isinstance(names, dict):
                    names[key] = label
        save_store(home, items)

    def recognize(u: dict):
        m = u.get("message") or u.get("my_chat_member") or {}
        chat = m.get("chat") or {}
        if m.get("date", 0) < since:
            return None
        if chat.get("type") == "private" and accepts_person and (m.get("from") or {}).get("id"):
            sender = m["from"]
            label = str(sender.get("first_name") or sender.get("username") or "")
            list_add(home, f"TELEGRAM_ALLOWED_USERS_{up}", str(sender["id"]))
            remember("people_names", str(sender["id"]), label)
            return {"person": label}
        if chat.get("type") in ("group", "supergroup") and accepts_group and chat.get("id"):
            label = str(chat.get("title") or "")
            list_add(home, f"TELEGRAM_GROUP_ALLOWED_CHATS_{up}", str(chat["id"]))
            remember("group_names", str(chat["id"]), label)
            return {"group": label}
        return None

    found: list[dict] = []

    def gather(ups: list) -> None:
        """Reconhece o lote inteiro: várias pessoas podem mandar na mesma fatia."""
        nonlocal offset
        for u in ups:
            offset = u["update_id"] + 1
            r = recognize(u)
            if r and r not in found:
                found.append(r)

    def finish():
        if offset:
            try:
                tg_api(token, "getUpdates", offset=offset, timeout=0)
            except TelegramError:
                pass
        if stopped_gateway:
            hermes_gateway("start")
        # a página segue esperando até PARAR ou 3 min: o bot só volta ao gateway quando a espera inteira (desde
        # `since`) acaba, ou no próximo colocar em uso; assim nada se perde entre duas fatias
        if time.time() - since >= 170:
            unpark()
        if found:
            return out({"ok": True, "recognized": found, **found[0]})
        return out({"ok": False, "error": T("nenhuma mensagem chegou ao bot"), "timed_out": True})

    try:
        # o que ficou pendente (as mensagens antigas são descartadas por `since`)
        gather(tg_api(token, "getUpdates", timeout=0, allowed_updates='["message","my_chat_member"]'))
        if found:
            return finish()
    except TelegramError as e:
        if e.code != 409:
            return out({"ok": False, "error": T("não consegui falar com o Telegram")})
    while time.time() < deadline:
        try:
            ups = tg_api(token, "getUpdates", http_timeout=35, offset=offset, allowed_updates='["message","my_chat_member"]',
                         timeout=min(25, max(1, int(deadline - time.time()))))
        except TelegramError as e:
            if e.code == 409:
                if not stopped_gateway:
                    hermes_gateway("stop")
                    stopped_gateway = True
                time.sleep(2)
                continue
            if stopped_gateway:
                hermes_gateway("start")
            return out({"ok": False, "error": T("o Telegram não entregou mensagens; tente de novo")})
        gather(ups)
        if found:
            return finish()
    return finish()


# ---------------------------------------------------------------- Escritório (Paperclip como motor, Hermes como executor)
def office_desired(home: Path) -> bool | None:
    """O que a pessoa decidiu na página (loja) ou, sem decisão, o que o tenant diz."""
    try:
        store = json.loads((home / "office.json").read_text(encoding="utf-8"))
        if isinstance(store, dict) and "enabled" in store:
            return bool(store["enabled"])
    except (OSError, ValueError):
        pass
    for cand in (Path((home / "tenant.path").read_text(encoding="utf-8").strip()) if (home / "tenant.path").is_file() else None, home / "jarvis.yaml"):
        try:
            if cand and cand.is_file():
                return re.search(r"^office:\s*\n(?:\s+.*\n)*?\s+enabled:\s*true", cand.read_text(encoding="utf-8"), re.M) is not None
        except OSError:
            continue
    return None


def office_status(home: Path, roles: list[str]) -> dict:
    """Ligado?, aplicado?, os cargos (catálogo + estado no Paperclip), as rotinas (catálogo + última/próxima) e as
    tarefas (lidas do Paperclip local, se estiver no ar)."""
    out_: dict = {"available": "core" in roles, "enabled": bool(office_desired(home)), "applied": False, "position": None,
                  "positions": [], "routines": [], "tasks": [], "error": None}
    try:
        out_["applied"] = bool(json.loads((home / "render" / "office.json").read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        pass
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_office_mcp as office_mcp  # mesmo diretório; só stdlib
        import jarvis_routines as routines_lib
    except ImportError:
        return out_
    if not out_["enabled"] and not out_["applied"]:
        return out_
    from datetime import datetime
    cat = routines_lib.catalog(home)
    ids = routines_lib.position_ids(home)
    agents: dict = {}
    if out_["applied"]:
        try:
            for ag in office_mcp.as_list(office_mcp.api("GET", f"/companies/{office_mcp.company_id()}/agents"), "agents"):
                agents[ag.get("id")] = ag
        except RuntimeError as e:
            out_["error"] = tv(str(e)[:200])
    by_agent: dict = {}
    for c in cat["positions"]:
        reg = ids.get(c["name"]) or {}
        ag = agents.get(reg.get("id")) or {}
        item = {"name": c["name"], "title": tv(c["title"]), "does": tv(c["does"]), "level": c.get("level") or "", "default": c["default"],
                "applied": bool(reg.get("id")) and c["applied"], "state": ag.get("status") or "", "tasks_done": 0}
        if reg.get("id"):
            by_agent[reg["id"]] = item
        out_["positions"].append(item)
    default = next((c for c in out_["positions"] if c["name"] == "work"), None)
    if default and default["applied"]:
        folder = str(Path(read_env(home / "profile.env").get("VAULT_PATH") or (Path.home() / "whitebeard-secondbrain")) / "tenants" / (read_env(home / "profile.env").get("JARVIS_TENANT") or "personal") / "projects-doc" / "office")
        out_["position"] = {"name": default["title"], "state": default["state"], "folder": folder}
    courier_state = routines_lib.read_json(home / "office" / "courier.json", {})
    done_routines = (courier_state.get("routines") if isinstance(courier_state, dict) else None) or {}
    now = datetime.now()
    for r in cat["routines"]:
        reg = done_routines.get(r["name"]) or {}
        try:
            nxt = routines_lib.next_run(r["when"], now) if r["enabled"] else None
        except ValueError:
            nxt = None
        out_["routines"].append({**r, "title": tv(r["title"]), "does": tv(r["does"]), "when_text": routines_lib.when_text(r["when"]), "last": reg.get("last") or "", "last_task": reg.get("task") or "",
                                "next": nxt.strftime("%Y-%m-%dT%H:%M") if nxt else ""})
    out_["tokens"] = {"month": {}, "positions": [], "routines": [], "tasks": [], "alerts": []}
    if not out_["applied"]:
        return out_
    try:
        import jarvis_office_tokens as tok
        out_["tokens"] = translated_tokens(tok.with_cache(home, cat["positions"], ids))
    except Exception:  # sem tokens a página continua; o resto do status não depende disto
        pass
    try:
        items = office_mcp.as_list(office_mcp.api("GET", f"/companies/{office_mcp.company_id()}/issues"), "issues")
        items.sort(key=lambda i: i.get("updatedAt") or "", reverse=True)
        for it in items:
            if it.get("status") == "done" and it.get("assigneeAgentId") in by_agent:
                by_agent[it["assigneeAgentId"]]["tasks_done"] += 1
        for it in items[:40]:
            desc = it.get("description") or ""
            t = {"id": it.get("id"), "number": it.get("identifier"), "title": tv(it.get("title")), "state": it.get("status"),
                 "created_at": it.get("createdAt"), "updated_at": it.get("updatedAt"),
                 "origin": (re.search(r"\[origin:\s*([^\]]+)\]", desc) or [None, ""])[1] if "[origin:" in desc else "",
                 "routine": routines_lib.routine_of_task(desc), "position": (by_agent.get(it.get("assigneeAgentId")) or {}).get("title") or "",
                 "delivery": "", "question": "", "pending_id": ""}
            if it.get("status") in ("done", "cancelled", "blocked", "in_progress"):
                cs = office_mcp.as_list(office_mcp.api("GET", f"/issues/{it['id']}/comments"), "comments")
                cs.sort(key=lambda c: c.get("createdAt") or "")
                for c in reversed(cs):
                    b = (c.get("body") or "").strip()
                    if b.upper().startswith(("DELIVERY:", "PENDING:", "REJECTED")):
                        t["delivery"] = b[:600]
                        break
            if it.get("status") in ("blocked", "in_progress", "todo"):
                for p_ in office_mcp.pending_of(it["id"]):
                    t["question"] = ((p_.get("payload") or {}).get("prompt") or "")[:600]
                    t["pending_id"] = p_.get("id", "")
                    break
            out_["tasks"].append(t)
    except RuntimeError as e:
        out_["error"] = tv(str(e)[:200])
    return out_


def translated_tokens(tokens: dict) -> dict:
    """Os nomes e as dicas que o cálculo de tokens devolve (e guarda em português no cache), no idioma da interface."""
    if not isinstance(tokens, dict):
        return tokens
    out_ = dict(tokens)
    for key, fields in (("positions", ("title",)), ("routines", ("title",)), ("tasks", ("title",)), ("providers", ("title",)),
                          ("alerts", ("text", "hint"))):
        if isinstance(out_.get(key), list):
            out_[key] = [{**x, **{k: tv(x[k]) for k in fields if k in x}} if isinstance(x, dict) else x for x in out_[key]]
    return out_


def cmd_office_set(home: Path) -> int:
    raw = stdin_value()
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "error": T("dados inválidos")})
    enabled = bool(payload.get("enabled"))
    store = office_store(home)
    store["enabled"] = enabled
    save_office_store(home, store)
    return out({"ok": True, "enabled": enabled})


def office_store(home: Path) -> dict:
    try:
        d = json.loads((home / "office.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_office_store(home: Path, store: dict) -> None:
    (home / "office.json").write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stdin_json() -> dict | None:
    try:
        d = json.loads(stdin_value() or "{}")
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


def cmd_office_position(home: Path, action: str, name: str = "") -> int:
    """Cargos criados pela página: ficam em office.json (cargos) e passam a valer depois de COLOCAR EM USO
    (perfil do Hermes + agente no Paperclip). O cargo `work` não se apaga."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import jarvis_routines as routines_lib
    store = office_store(home)
    positions = [c for c in (store.get("positions") or []) if isinstance(c, dict) and c.get("name")]
    if action == "remove":
        if name == "work":
            return out({"ok": False, "error": T("o Assistente de trabalho é o cargo padrão: não se apaga")})
        exists = any(c["name"] == name and not c.get("removed") for c in positions) or any(c["name"] == name for c in routines_lib.catalog(home)["positions"])
        if not exists:
            return out({"ok": False, "error": T("não há cargo chamado {name}", name=name)})
        positions = [c for c in positions if c["name"] != name] + [{"name": name, "removed": True}]
        store["positions"] = positions
        save_office_store(home, store)
        return out({"ok": True, "name": name})
    payload = stdin_json()
    if payload is None:
        return out({"ok": False, "error": T("dados inválidos")})
    if action == "add":
        name = str(payload.get("name") or "").strip().lower()
        if not routines_lib.NAME_RE.match(name):
            return out({"ok": False, "error": T("nome: só letras minúsculas, números e hífen, começando por letra (2 a 31 caracteres)")})
        if name == "work" or any(c["name"] == name for c in routines_lib.catalog(home)["positions"]):
            return out({"ok": False, "error": T("já existe um cargo chamado {name}", name=name)})
        if (hermes_profile_dir(home, name)).is_dir() or any(a.get("name") == name and not a.get("removed") for a in load_store(home)):
            return out({"ok": False, "error": T("já existe um assistente ou perfil chamado {name}; escolha outro nome", name=name)})
        new_position = routines_lib.normalize_position({**payload, "name": name})
    else:
        current = next((c for c in routines_lib.catalog(home)["positions"] if c["name"] == name), None)
        if current is None:
            return out({"ok": False, "error": T("não há cargo chamado {name}", name=name)})
        new_position = routines_lib.normalize_position({**current, **{k: v for k, v in payload.items() if k in ("title", "does", "level")}, "name": name})
    if not new_position["title"]:
        return out({"ok": False, "error": T("o cargo precisa de um título")})
    positions = [c for c in positions if c["name"] != name] + [new_position]
    store["positions"] = positions
    save_office_store(home, store)
    return out({"ok": True, "position": new_position})


def cmd_office_routine(home: Path, action: str, name: str = "") -> int:
    """Rotinas criadas pela página: office/routines.json. Valem na hora (o carteiro lê de lá a cada minuto)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import jarvis_routines as routines_lib
    cat = routines_lib.catalog(home)
    store = routines_lib.routines_store(home)
    if action == "run":
        r = next((x for x in cat["routines"] if x["name"] == name), None)
        if r is None:
            return out({"ok": False, "error": T("não há rotina chamada {name}", name=name)})
        try:
            t = routines_lib.fire(home, r)
        except RuntimeError as e:
            return out({"ok": False, "error": tv(str(e)[:200])})
        state_path = home / "office" / "courier.json"
        state = routines_lib.read_json(state_path, {})
        if isinstance(state, dict):
            state.setdefault("routines", {})[name] = {**(state.get("routines", {}).get(name) or {}), "task": t.get("number") or "", "at": int(time.time()), "manual": True}
            routines_lib.write_json(state_path, state)
        return out({"ok": True, "task": t})
    if action == "remove":
        if not any(x["name"] == name for x in cat["routines"]):
            return out({"ok": False, "error": T("não há rotina chamada {name}", name=name)})
        store = [x for x in store if x["name"] != name] + [{"name": name, "removed": True}]
        routines_lib.write_json(home / "office" / "routines.json", store)
        return out({"ok": True, "name": name})
    payload = stdin_json()
    if payload is None:
        return out({"ok": False, "error": T("dados inválidos")})
    if action == "add":
        name = str(payload.get("name") or "").strip().lower()
        if any(x["name"] == name for x in cat["routines"]):
            return out({"ok": False, "error": T("já existe uma rotina chamada {name}", name=name)})
        new_routine = routines_lib.normalize_routine({**payload, "name": name})
    else:
        current = next((x for x in cat["routines"] if x["name"] == name), None)
        if current is None:
            return out({"ok": False, "error": T("não há rotina chamada {name}", name=name)})
        new_routine = routines_lib.normalize_routine({**current, **{k: v for k, v in payload.items() if k in ("title", "does", "when", "where", "position", "enabled")}, "name": name})
    error = routines_lib.validate_routine(new_routine, cat["positions"])
    if error:
        return out({"ok": False, "error": tv(error)})
    store = [x for x in store if x["name"] != name] + [new_routine]
    routines_lib.write_json(home / "office" / "routines.json", store)
    return out({"ok": True, "routine": new_routine})


def cmd_office_decide(home: Path, task: str, approve: bool) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_office_mcp as office_mcp
    except ImportError:
        return out({"ok": False, "error": T("ferramentas do Escritório ausentes")})
    note = stdin_value()
    try:
        r = office_mcp.approve(task, note) if approve else office_mcp.reject(task, note)
    except RuntimeError as e:
        return out({"ok": False, "error": tv(str(e)[:200])})
    return out(r)


def tenant_hermes(home: Path) -> dict:
    """O bloco hermes: do tenant (provider/model/subscription/level), lido por regex: o setup não usa PyYAML."""
    out: dict = {}
    for cand in ([Path((home / "tenant.path").read_text(encoding="utf-8").strip())] if (home / "tenant.path").is_file() else []) + [home / "jarvis.yaml"]:
        try:
            txt = cand.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^hermes:\s*\n((?:[ \t]+.*\n?)*)", txt, re.M)
        if m:
            for k, v in re.findall(r"^[ \t]+(provider|model|subscription|level):\s*([^\s#]+)", m.group(1), re.M):
                out[k] = v.strip("'\"")
            break
    return out


def thinker_status(home: Path, roles: list[str]) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_thinker as thinker
    except ImportError:
        return {"available": False}
    try:
        st = thinker.status(home, Path(__file__).resolve().parent.parent, tenant_hermes(home))
        # nomes dos níveis (Rápido, Equilibrado, Mais capaz) e o erro do login guardado em thinker.login.json
        if isinstance(st.get("levels"), list):
            st["levels"] = [{**n, "name": tv(n.get("name"))} if isinstance(n, dict) else n for n in st["levels"]]
        if isinstance(st.get("login"), dict) and st["login"].get("error"):
            st["login"] = {**st["login"], "error": tv(st["login"]["error"])}
        return {"available": "core" in roles, **st}
    except Exception as e:  # o status geral não pode cair por causa disto
        return {"available": "core" in roles, "error": tv(str(e)[:200])}


def cmd_thinker(home: Path, action: str, arg: str = "") -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import jarvis_thinker as thinker
    if action == "set":
        payload = stdin_json()
        if payload is None:
            return out({"ok": False, "error": T("dados inválidos")})
        return out(thinker.choose(home, payload.get("provider"), payload.get("level")))
    if action == "login":
        return out(thinker.start_login(home, arg))
    if action == "login-status":
        state = thinker.refresh_login(home)
        return out({"ok": True, "login": {k: (tv(v) if k == "error" else v) for k, v in state.items() if k != "pid"}})
    if action == "login-cancel":
        return out(thinker.cancel_login(home))
    if action == "logout":
        return out(thinker.logout(home, arg))
    if action == "key":
        # a chave vem pela entrada padrão e nunca volta na resposta
        return out(thinker.connect_key(home, arg, stdin_value()))
    return 2


def cmd_status(home: Path) -> int:
    clear_stale_waits(home)
    profile = read_env(home / "profile.env")
    secrets_path = home / "secrets.env"
    filled = {k: bool(v) for k, v in read_env(secrets_path).items()}
    roles = [r for r in profile.get("JARVIS_ROLE", "").split(",") if r]
    declared = declared_secrets(home)
    if not declared:
        # instalação de um kernel sem secrets.json: só os nomes do secrets.env
        declared = [{"name": k} for k in filled]
    secret_list = []
    for s in declared:
        name = s.get("name", "")
        if not name or name in HIDDEN or s.get("generated"):
            continue
        secret_list.append({
            "name": name,
            "title": secret_text(s.get("title", "")),
            "description": secret_text(s.get("description", "")),
            "obtain": secret_text(s.get("obtain", "")),
            "required": bool(s.get("required")),
            "implicit": bool(s.get("implicit")),
            "assistant": s.get("assistant") or "",
            "filled": filled.get(name, False),
        })
    secret_list.sort(key=lambda s: (not s["required"], s["filled"], s["name"]))
    bot = ""
    try:
        bot = (home / "telegram.bot").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return out({
        "ok": True,
        "tenant": profile.get("JARVIS_TENANT", ""),
        "roles": roles,
        "data": profile.get("JARVIS_DATA", ""),
        "vault": profile.get("VAULT_PATH", ""),
        "telegram": {
            "available": "core" in roles,
            "token": filled.get("TELEGRAM_BOT_TOKEN", False),
            "chat": filled.get("TELEGRAM_HOME_CHANNEL", False),
            "declined": (home / "telegram.decision").is_file(),
            "bot": bot,
        },
        "secrets": secret_list,
        "assistants": assistants_status(home, filled),
        "office": office_status(home, roles),
        "thinker": thinker_status(home, roles),
        "done": (home / "setup.done").is_file(),
        "pending": pending_apply(home),
    })


# ---------------------------------------------------------------- set-secret
def cmd_set_secret(home: Path, name: str) -> int:
    if not NAME_RE.match(name or ""):
        return out({"ok": False, "error": T("nome de segredo inválido")})
    declared = declared_secrets(home)
    known = {s.get("name") for s in declared} | set(read_env(home / "secrets.env"))
    generated = {s.get("name") for s in declared if s.get("generated")}
    # os gerados pelo kernel (senha do banco, tokens de bootstrap) não são da pessoa: trocá-los quebraria
    # o que já foi criado com eles
    if name not in known or name in HIDDEN or name in generated:
        return out({"ok": False, "error": T("{name} não é um segredo deste tenant", name=name)})
    value = stdin_value()
    if "\n" in value:
        return out({"ok": False, "error": T("o valor tem mais de uma linha")})
    set_secret(home / "secrets.env", name, value)
    return out({"ok": True, "name": name, "filled": bool(value)})


# ---------------------------------------------------------------- telegram
class TelegramError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(msg)
        self.code = code


class TelegramConnection(http.client.HTTPSConnection):
    """HTTPS que tenta IPv4 antes de IPv6, com conexão curta por endereço.

    Em muitas redes domésticas o IPv6 é anunciado mas não chega à internet: o Python tentaria o
    endereço IPv6 primeiro e só desistiria no fim do timeout inteiro (35 s parado antes de responder).
    Depois de conectado, o timeout volta ao valor pedido (o long-poll do getUpdates precisa dele).
    """

    CONNECT_TIMEOUT = 6

    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda i: i[0] != socket.AF_INET)
        err: OSError | None = None
        sock = None
        for fam, typ, proto, _, addr in infos:
            s = socket.socket(fam, typ, proto)
            s.settimeout(self.CONNECT_TIMEOUT)
            try:
                s.connect(addr)
                sock = s
                break
            except OSError as e:
                err = e
                s.close()
        if sock is None:
            raise err or OSError(f"sem rota para {self.host}")
        sock.settimeout(self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def tg_api(token: str, method: str, http_timeout: int = 40, **q):
    """Chama a API do bot; `q` vira query string (o `timeout` dela é o long-poll do Telegram)."""
    path = f"/bot{token}/{method}"
    if q:
        path += "?" + urllib.parse.urlencode(q)
    conn = TelegramConnection("api.telegram.org", timeout=http_timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise TelegramError(resp.status, f"HTTP {resp.status}")
        return json.loads(body)["result"]
    except TelegramError:
        raise
    except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
        raise TelegramError(None, str(e)) from e
    finally:
        conn.close()


def cmd_telegram_token(home: Path) -> int:
    token = stdin_value()
    if not token:
        return out({"ok": False, "error": T("token vazio")})
    try:
        me = tg_api(token, "getMe", http_timeout=20)
    except TelegramError as e:
        if e.code in (401, 404):
            return out({"ok": False, "error": T("o Telegram recusou o token (confira em @BotFather)")})
        return out({"ok": False, "error": T("não consegui falar com o Telegram; confira a internet e tente de novo")})
    username = str(me.get("username") or "")
    set_secret(home / "secrets.env", "TELEGRAM_BOT_TOKEN", token)
    (home / "telegram.bot").write_text(username + "\n", encoding="utf-8")
    try:
        (home / "telegram.decision").unlink()
    except OSError:
        pass
    return out({"ok": True, "bot": username})


def hermes_bin() -> str | None:
    return shutil.which("hermes") or shutil.which("hermes", path=str(Path.home() / ".local" / "bin"))


def hermes_gateway(action: str) -> None:
    h = hermes_bin()
    if not h:
        return
    try:
        subprocess.run([h, "gateway", action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_telegram_wait(home: Path, seconds: int, since: int) -> int:
    token = read_env(home / "secrets.env").get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return out({"ok": False, "error": T("sem token do bot")})
    seconds = max(5, min(seconds, 300))
    deadline = time.time() + seconds
    offset = 0
    stopped_gateway = False
    try:
        # o backlog pode ter mensagem de qualquer pessoa: só vale o que chegar depois de `since`
        last = tg_api(token, "getUpdates", offset=-1, timeout=0)
        if last:
            offset = last[-1]["update_id"] + 1
            # a última pode ser a própria mensagem do dono, mandada antes de este comando começar
            m = last[-1].get("message") or {}
            if m.get("date", 0) >= since and (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    except TelegramError as e:
        if e.code != 409:
            return out({"ok": False, "error": T("não consegui falar com o Telegram")})
    while time.time() < deadline:
        try:
            ups = tg_api(token, "getUpdates", http_timeout=35, offset=offset, allowed_updates='["message"]',
                         timeout=min(25, max(1, int(deadline - time.time()))))
        except TelegramError as e:
            if e.code == 409 and not stopped_gateway:
                # outro leitor (o gateway do Hermes com este token) disputa as mensagens: para e devolve no fim
                hermes_gateway("stop")
                stopped_gateway = True
                time.sleep(2)
                continue
            if e.code == 409:
                time.sleep(2)
                continue
            if stopped_gateway:
                hermes_gateway("start")
            return out({"ok": False, "error": T("o Telegram não entregou mensagens; tente de novo")})
        for u in ups:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("date", 0) < since:
                continue
            if (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": False, "error": T("nenhuma mensagem chegou ao bot"), "timed_out": True})


def finish_wait(home: Path, token: str, offset: int, m: dict, stopped_gateway: bool) -> int:
    try:
        tg_api(token, "getUpdates", offset=offset, timeout=0)  # marca como lida: o gateway não responde a ela de novo
    except TelegramError:
        pass
    sender = m.get("from") or {}
    set_secret(home / "secrets.env", "TELEGRAM_HOME_CHANNEL", str(sender["id"]))
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": True, "chat": True, "name": str(sender.get("first_name") or "")})


def cmd_telegram_off(home: Path) -> int:
    (home / "telegram.decision").write_text("no\n", encoding="utf-8")
    return out({"ok": True})


def cmd_done(home: Path) -> int:
    (home / "setup.done").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return out({"ok": True})


# ---------------------------------------------------------------- atualização
# O kernel instalado é um clone git (install.sh). Versão nova = a origem no GitHub tem commit que este clone
# não contém. O JARVIS Web instalado é o alvo do link app/jarvis-web (app/jarvis-web-<versão>), e a versão
# nova vem do versions.env desse commit. A rede é consultada no máximo uma vez por --max-age (update.json);
# a parte local é recalculada sempre, então depois de atualizar o aviso some sozinho.
DEFAULT_REPO = "https://github.com/whitebeardit/whitebeard-os.git"
GITHUB_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
RELEASE_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/releases/download/([^/]+)/")
TOKEN_RE = re.compile(r"token=[0-9a-f]{16,}")


def https_get(host: str, path: str, timeout: int = 15, accept: str = "*/*") -> bytes:
    conn = TelegramConnection(host, timeout=timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "whitebeard-os", "Accept": accept})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status} em {host}{path}")
        return body
    finally:
        conn.close()


def kernel_dir(home: Path) -> Path | None:
    try:
        d = Path((home / "kernel.path").read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return d if (d / "bootstrap.sh").is_file() else None


def git(kernel: Path, *args: str, timeout: int = 20) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    try:
        r = subprocess.run(["git", "-C", str(kernel), *args], capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_ok(kernel: Path, *args: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(kernel), *args], capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def local_versions(home: Path, kernel: Path) -> dict:
    web = ""
    try:
        name = os.path.basename(os.path.realpath(home / "app" / "jarvis-web"))
        web = name[len("jarvis-web-"):] if name.startswith("jarvis-web-") else ""
    except OSError:
        pass
    # o ramo acompanhado na origem (origin/main numa instalação); um ramo local sem origem compara com main
    up = git(kernel, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    branch = up.split("/", 1)[1] if up.startswith("origin/") and "/" in up else "main"
    return {"web": web, "kernel": git(kernel, "rev-parse", "HEAD"), "branch": branch}


def remote_versions(kernel: Path, branch: str) -> dict:
    m = GITHUB_RE.match(git(kernel, "remote", "get-url", "origin") or DEFAULT_REPO)
    if not m:
        raise OSError("a origem do kernel não está no GitHub")
    owner, name = m.group(1), m.group(2)
    # sempre por HTTPS: o repositório é público e o serviço não tem agente SSH
    out_ = git(kernel, "ls-remote", f"https://github.com/{owner}/{name}.git", f"refs/heads/{branch}", timeout=25)
    if not out_:
        raise OSError("não consegui consultar o GitHub")
    sha = out_.split()[0]
    body = https_get("raw.githubusercontent.com", f"/{owner}/{name}/{sha}/kernel/versions.env").decode("utf-8", "replace")
    vals = {}
    for line in body.splitlines():
        if line.startswith("JARVIS_WEB_") and "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v.strip().strip("'\"")
    return {"kernel": sha, "web": vals.get("JARVIS_WEB_VERSION", ""), "web_url": vals.get("JARVIS_WEB_URL", "")}


def release_notes(web_url: str) -> str:
    """Notas da release do pacote novo (GitHub Releases, público). Só enfeite: qualquer falha vira ''."""
    m = RELEASE_RE.match(web_url or "")
    if not m:
        return ""
    try:
        body = https_get("api.github.com", f"/repos/{m.group(1)}/{m.group(2)}/releases/tags/{m.group(3)}",
                         timeout=10, accept="application/vnd.github+json")
        notes = str(json.loads(body).get("body") or "").strip()
    except (OSError, ValueError):
        return ""
    return notes[:600]


def cmd_update_check(home: Path, max_age: int, force: bool) -> int:
    kernel = kernel_dir(home)
    if not kernel:
        return out({"ok": False, "error": T("kernel não encontrado (kernel.path)")})
    local = local_versions(home, kernel)
    cache_path = home / "update.json"
    cached: dict = {}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    now = int(time.time())
    age = now - int(cached.get("checked_at") or 0)
    # uma falha de rede vale por menos tempo que uma resposta: a próxima abertura tenta de novo
    fresh = bool(cached) and age < (min(max_age, 3600) if cached.get("error") else max_age)
    queried = force or not fresh
    if queried:
        try:
            remote = remote_versions(kernel, local["branch"])
            notes = release_notes(remote["web_url"]) if remote["web"] and remote["web"] != local["web"] else ""
            cached = {"checked_at": now, "remote": remote, "notes": notes}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            cached = {"checked_at": now, "remote": cached.get("remote"), "notes": cached.get("notes", ""),
                      "error": "não consegui consultar as versões novas (sem internet?)", "detail": str(e)[:160]}
        try:
            cache_path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
        except OSError:
            pass
    remote = cached.get("remote") or {}
    sha = remote.get("kernel", "")
    # novo = a origem tem um commit que este clone não contém (um clone adiantado, de quem desenvolve, não conta)
    kernel_new = bool(sha) and sha != local["kernel"] and not (
        git_ok(kernel, "cat-file", "-e", f"{sha}^{{commit}}") and git_ok(kernel, "merge-base", "--is-ancestor", sha, "HEAD"))
    # o pacote do JARVIS Web também conta: o clone pode estar em dia sem o bootstrap ter rodado depois
    web_new = bool(remote.get("web")) and remote["web"] != local["web"]
    available = kernel_new or web_new
    return out({
        "ok": True,
        "available": available,
        "local": {"web": local["web"], "kernel": local["kernel"]},
        "remote": {"web": remote.get("web", ""), "kernel": sha},
        "notes": cached.get("notes", "") if available else "",
        "checked_at": cached.get("checked_at"),
        "error": tv(cached.get("error")),  # guardado em português no update.json; traduzido só aqui
        "queried": queried,
    })


def update_paths(home: Path) -> tuple[Path, Path, Path]:
    return home / "update.run.json", home / "update.exit", home / "logs" / "update.log"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def run_state(home: Path) -> dict:
    run_path, exit_path, log_path = update_paths(home)
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": False, "running": False, "exit_code": None}
    exit_code = None
    try:
        exit_code = int(exit_path.read_text(encoding="utf-8").strip() or -1)
    except (OSError, ValueError):
        pass
    if exit_code is not None:
        running = False
    elif run.get("unit"):
        try:
            running = subprocess.run(["systemctl", "--user", "is-active", "--quiet", str(run["unit"])],
                                     capture_output=True, timeout=10, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            running = False
    else:
        running = pid_alive(int(run.get("pid") or 0))
    if exit_code is None and not running:
        exit_code = -1  # parou sem escrever o código: morto no meio
    finish = None
    if exit_code is not None:
        try:
            finish = int(exit_path.stat().st_mtime)
        except OSError:
            finish = int(time.time())
    return {"exists": True, "running": running, "exit_code": exit_code, "started_at": run.get("started_at"), "finished_at": finish, "log": str(log_path)}


def cmd_update_run(home: Path) -> int:
    kernel = kernel_dir(home)
    if not kernel:
        return out({"ok": False, "error": T("kernel não encontrado (kernel.path)")})
    install = kernel / "install.sh"
    if not install.is_file():
        return out({"ok": False, "error": T("install.sh não encontrado no kernel")})
    if run_state(home)["running"]:
        return out({"ok": False, "error": T("já há uma atualização em andamento")})
    run_path, exit_path, log_path = update_paths(home)
    profile = read_env(home / "profile.env")
    local = local_versions(home, kernel)
    try:
        tenant = (home / "tenant.path").read_text(encoding="utf-8").strip()
    except OSError:
        tenant = ""
    if not tenant:
        return out({"ok": False, "error": T("tenant.path ausente: não sei qual tenant reinstalar")})
    args = ["sh", str(install), "--no-prompt"]
    if profile.get("JARVIS_ROLE"):
        args += ["--role", profile["JARVIS_ROLE"]]
    wrapper = f"{shlex.join(args)} >{shlex.quote(str(log_path))} 2>&1; echo $? >{shlex.quote(str(exit_path))}"
    env_extra = {"JARVIS_TENANT": tenant, "WHITEBEARD_OS_DIR": str(kernel), "WHITEBEARD_OS_REF": local["branch"],
                 "JARVIS_HOME": str(home), "HOME": str(Path.home())}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for p in (exit_path, home / "update.json"):
        try:
            p.unlink()
        except OSError:
            pass
    now = int(time.time())
    record: dict = {"started_at": now, "log": str(log_path), "from": local}
    # Linux: unidade própria do systemd (o restart do jarvis-web mata o cgroup inteiro, e o filho iria junto).
    # macOS e sem systemd: sessão nova (setsid); o launchd só derruba o grupo de processos do serviço.
    started = False
    if sys.platform.startswith("linux") and shutil.which("systemd-run"):
        unit = f"whitebeard-update-{now}"
        cmd = ["systemd-run", "--user", "--quiet", "--collect", "--unit", unit]
        cmd += [f"--setenv={k}={v}" for k, v in env_extra.items()]
        cmd += ["sh", "-c", wrapper]
        try:
            started = subprocess.run(cmd, capture_output=True, timeout=30, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            started = False
        if started:
            record["unit"] = unit
    if not started:
        try:
            p = subprocess.Popen(["sh", "-c", wrapper], cwd=str(kernel), env={**os.environ, **env_extra},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError as e:
            return out({"ok": False, "error": T("não consegui iniciar a atualização: {error}", error=e)})
        record["pid"] = p.pid
    run_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return out({"ok": True, "started_at": now})


def cmd_update_status(home: Path) -> int:
    st = run_state(home)
    lines: list[str] = []
    if st["exists"]:
        try:
            raw = Path(st["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raw = []
        for line in raw[-15:]:
            line = TOKEN_RE.sub("token=…", line.replace("[whitebeard-os] ", "").replace("[whitebeard-os/install] ", "")).rstrip()
            if line:
                lines.append(line)
    kernel = kernel_dir(home)
    local = local_versions(home, kernel) if kernel else {"web": "", "kernel": ""}
    return out({"ok": True, **st, "lines": lines, "local": {"web": local["web"], "kernel": local["kernel"]}})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("set-secret")
    p.add_argument("name")
    sub.add_parser("telegram-token")
    p = sub.add_parser("telegram-wait")
    p.add_argument("--seconds", type=int, default=170)
    p.add_argument("--since", type=int, default=0)
    sub.add_parser("telegram-off")
    sub.add_parser("done")
    p = sub.add_parser("update-check")
    p.add_argument("--max-age", type=int, default=86400)
    p.add_argument("--force", action="store_true")
    sub.add_parser("update-run")
    sub.add_parser("update-status")
    sub.add_parser("assistant-add")
    p = sub.add_parser("assistant-remove")
    p.add_argument("name")
    p = sub.add_parser("assistant-forget")
    p.add_argument("name")
    p.add_argument("which", choices=["person", "group"])
    p.add_argument("id")
    p = sub.add_parser("assistant-update")
    p.add_argument("name")
    p = sub.add_parser("assistant-token")
    p.add_argument("name")
    p = sub.add_parser("assistant-wait")
    p.add_argument("name")
    p.add_argument("--seconds", type=int, default=170)
    p.add_argument("--since", type=int, default=0)
    for c in ("assistant-whatsapp-pair", "assistant-whatsapp-status", "assistant-whatsapp-unpair"):
        p = sub.add_parser(c)
        p.add_argument("name")
    sub.add_parser("office-set")
    for c in ("office-approve", "office-reject"):
        p = sub.add_parser(c)
        p.add_argument("task")
    sub.add_parser("office-position-add")
    sub.add_parser("office-routine-add")
    for c in ("office-position-update", "office-position-remove", "office-routine-update", "office-routine-remove", "office-routine-run"):
        p = sub.add_parser(c)
        p.add_argument("name")
    sub.add_parser("thinker-set")
    sub.add_parser("thinker-login-status")
    sub.add_parser("thinker-login-cancel")
    for c in ("thinker-login", "thinker-logout", "thinker-key"):
        p = sub.add_parser(c)
        p.add_argument("provider")
    args = ap.parse_args()
    home = Path(args.home).expanduser()
    if not (home / "profile.env").is_file():
        return out({"ok": False, "error": T("instalação não encontrada em {home}", home=home)})
    if args.cmd == "status":
        return cmd_status(home)
    if args.cmd == "set-secret":
        return cmd_set_secret(home, args.name)
    if args.cmd == "telegram-token":
        return cmd_telegram_token(home)
    if args.cmd == "telegram-wait":
        return cmd_telegram_wait(home, args.seconds, args.since or int(time.time()))
    if args.cmd == "telegram-off":
        return cmd_telegram_off(home)
    if args.cmd == "done":
        return cmd_done(home)
    if args.cmd == "update-check":
        return cmd_update_check(home, max(0, args.max_age), args.force)
    if args.cmd == "update-run":
        return cmd_update_run(home)
    if args.cmd == "update-status":
        return cmd_update_status(home)
    if args.cmd == "assistant-add":
        return cmd_assistant_add(home)
    if args.cmd == "assistant-remove":
        return cmd_assistant_remove(home, args.name)
    if args.cmd == "assistant-forget":
        return cmd_assistant_forget(home, args.name, args.which, args.id)
    if args.cmd == "assistant-update":
        return cmd_assistant_update(home, args.name)
    if args.cmd == "assistant-token":
        return cmd_assistant_token(home, args.name)
    if args.cmd == "assistant-wait":
        return cmd_assistant_wait(home, args.name, args.seconds, args.since or int(time.time()))
    if args.cmd == "assistant-whatsapp-pair":
        return cmd_assistant_whatsapp_pair(home, args.name)
    if args.cmd == "assistant-whatsapp-status":
        return cmd_assistant_whatsapp_status(home, args.name)
    if args.cmd == "assistant-whatsapp-unpair":
        return cmd_assistant_whatsapp_unpair(home, args.name)
    if args.cmd == "office-set":
        return cmd_office_set(home)
    if args.cmd == "office-approve":
        return cmd_office_decide(home, args.task, True)
    if args.cmd == "office-reject":
        return cmd_office_decide(home, args.task, False)
    if args.cmd.startswith("thinker-"):
        return cmd_thinker(home, args.cmd[len("thinker-"):], getattr(args, "provider", ""))
    if args.cmd.startswith("office-position-"):
        return cmd_office_position(home, args.cmd.rsplit("-", 1)[1], getattr(args, "name", ""))
    if args.cmd.startswith("office-routine-"):
        return cmd_office_routine(home, args.cmd.rsplit("-", 1)[1], getattr(args, "name", ""))
    return 2


if __name__ == "__main__":
    sys.exit(main())
