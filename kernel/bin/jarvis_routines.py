#!/usr/bin/env python3
"""jarvis_routines.py — o que o Escritório sabe sobre cargos e rotinas fora do render: o catálogo em uso, a loja
da página, a agenda (cron de 5 campos) e a criação da tarefa de uma rotina no Paperclip.

Usado pelo carteiro (agenda), pelo jarvis_setup.py (status e comandos da página) e pelo MCP office (lista de
cargos). Só biblioteca padrão. Nunca imprime segredo.

Arquivos:
  $JARVIS_HOME/render/office.json       o que o render aplicou: enabled, positions (tenant + página), rotinas do tenant
  $JARVIS_HOME/office.json              loja da página: {enabled, positions: [...]}  (cargos precisam de "colocar em uso")
  $JARVIS_HOME/office/routines.json      loja da página: [rotinas]               (valem na hora; o carteiro lê daqui)
  $JARVIS_HOME/office/positions.json       cargos criados no Paperclip: {name: {id, title}}
  $JARVIS_HOME/office/courier.json     estado do carteiro: avisos feitos e última execução de cada rotina
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from jarvis_i18n import T

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
DEFAULT_POSITION = {"name": "work", "title": "Assistente de trabalho",
                "does": "Executa o que o JARVIS e os assistentes delegam: pesquisas, documentos, propostas, resumos."}
DEFAULT_ROUTINE = {"name": "weekly-summary", "title": "Resumo da semana", "when": "0 8 * * 1", "where": "telegram", "position": "work", "enabled": True,
                 "does": ("Faça o resumo da semana do Escritório e do second brain: o que foi entregue, o que está pendente ou parado, "
                         "e o que mudou nas páginas do second brain nos últimos sete dias. Em tópicos curtos, em português do Brasil.")}
DAYS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"]


def days_for_display() -> list[str]:
    """Os dias da semana (0=domingo) no idioma da interface."""
    return [T("domingo"), T("segunda"), T("terça"), T("quarta"), T("quinta"), T("sexta"), T("sábado")]


# ---------------------------------------------------------------- arquivos
def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def routines_store(home: Path) -> list[dict]:
    d = read_json(home / "office" / "routines.json", [])
    return [r for r in d if isinstance(r, dict) and r.get("name")] if isinstance(d, list) else []


def positions_store(home: Path) -> list[dict]:
    d = read_json(home / "office.json", {})
    c = d.get("positions") if isinstance(d, dict) else None
    return [x for x in c if isinstance(x, dict) and x.get("name")] if isinstance(c, list) else []


def normalize_position(c: dict) -> dict:
    out = {"name": str(c.get("name") or "").strip(), "title": str(c.get("title") or c.get("name") or "").strip()[:80],
           "does": str(c.get("does") or "").strip()[:600]}
    if c.get("level") in ("fast", "balanced", "capable"):
        out["level"] = c["level"]  # vazio ou ausente: segue o nível geral de quem pensa
    return out


def normalize_routine(r: dict) -> dict:
    return {"name": str(r.get("name") or "").strip(), "title": str(r.get("title") or r.get("name") or "").strip()[:120],
            "does": str(r.get("does") or "").strip()[:2000], "when": str(r.get("when") or "").strip(),
            "where": str(r.get("where") or "telegram").strip(), "position": str(r.get("position") or "work").strip(),
            "enabled": bool(r.get("enabled", True))}


def merge(base: list[dict], store: list[dict], normalize) -> list[dict]:
    """A lista aplicada (tenant) com a loja da página por cima: nome repetido, vale o da página; `removed` esconde."""
    out = [normalize(x) for x in base if x.get("name")]
    for item in store:
        out = [x for x in out if x["name"] != item["name"]]
        if item.get("removed"):
            continue
        out.append(normalize(item))
    return out


def catalog(home: Path) -> dict:
    """O catálogo em uso: ligado, cargos (aplicados + loja, com `aplicado`) e rotinas (tenant + loja), com o cargo e a rotina padrão."""
    render = read_json(home / "render" / "office.json", {})
    if not isinstance(render, dict):
        render = {}
    enabled = bool(render.get("enabled"))
    store = read_json(home / "office.json", {})
    if isinstance(store, dict) and "enabled" in store:
        desired_enabled = bool(store["enabled"])
    else:
        desired_enabled = enabled
    applied = {c["name"] for c in render.get("positions") or [] if isinstance(c, dict) and c.get("name")}
    rendered_positions = [c for c in render.get("positions") or [] if isinstance(c, dict)]
    if not any(c.get("name") == "work" for c in rendered_positions):
        rendered_positions.insert(0, dict(DEFAULT_POSITION))
    positions = merge(rendered_positions, positions_store(home), normalize_position)
    for c in positions:
        c["applied"] = c["name"] in applied or (c["name"] == "work" and enabled)
        c["default"] = c["name"] == "work"
    rendered_routines = [r for r in render.get("routines") or [] if isinstance(r, dict)]
    if not any(r.get("name") == DEFAULT_ROUTINE["name"] for r in rendered_routines):
        rendered_routines.insert(0, dict(DEFAULT_ROUTINE))
    routines = merge(rendered_routines, routines_store(home), normalize_routine)
    for r in routines:
        r["default"] = r["name"] == DEFAULT_ROUTINE["name"]
    return {"enabled": enabled, "desired_enabled": desired_enabled, "positions": positions, "routines": routines}


def position_ids(home: Path) -> dict:
    """{nome: {id, titulo}} dos cargos criados no Paperclip. Instalação do P0/P1 (só position.json): o trabalho."""
    d = read_json(home / "office" / "positions.json", None)
    if isinstance(d, dict):
        return d
    legacy = read_json(home / "office" / "position.json", {})
    if isinstance(legacy, dict) and legacy.get("id"):
        return {"work": {"id": legacy["id"], "title": legacy.get("name") or DEFAULT_POSITION["title"], "company": legacy.get("company", "")}}
    return {}


# ---------------------------------------------------------------- cron de 5 campos
def _field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, p = part.split("/", 1)
            step = int(p)
            if step < 1:
                raise ValueError(T("passo inválido"))
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step > 1:
                b = hi
        if a < lo or b > hi or a > b:
            raise ValueError(T("fora do intervalo {lo}-{hi}: {part}", lo=lo, hi=hi, part=part))
        out.update(range(a, b + 1, step))
    return out


def parse_cron(when: str) -> dict:
    """Minutos, horas, dias do mês, meses e dias da semana (0=domingo) que a expressão aceita."""
    fields = (when or "").split()
    if len(fields) != 5:
        raise ValueError(T("a agenda precisa de 5 campos: minuto hora dia mês dia-da-semana"))
    mi, hr, dom, mo, dow = fields
    weekdays = {d % 7 for d in _field(dow, 0, 7)}
    return {"minutes": _field(mi, 0, 59), "hours": _field(hr, 0, 23), "month_days": _field(dom, 1, 31), "months": _field(mo, 1, 12),
            "weekdays": weekdays, "dom_any": dom.strip() == "*", "dow_any": dow.strip() == "*"}


def day_matches(c: dict, dt: datetime) -> bool:
    if dt.month not in c["months"]:
        return False
    dom_ok = dt.day in c["month_days"]
    dow_ok = ((dt.weekday() + 1) % 7) in c["weekdays"]  # python: segunda=0; cron: domingo=0
    if c["dom_any"] and c["dow_any"]:
        return True
    if c["dom_any"]:
        return dow_ok
    if c["dow_any"]:
        return dom_ok
    return dom_ok or dow_ok  # os dois restritos: vale um OU outro (regra do cron)


def matches(when: str, dt: datetime) -> bool:
    c = parse_cron(when)
    return dt.minute in c["minutes"] and dt.hour in c["hours"] and day_matches(c, dt)


def last_scheduled(when: str, now: datetime, window_minutes: int = 24 * 60) -> datetime | None:
    """O último horário da agenda até agora, dentro da janela (o Mac pode ter dormido; mais velho que isso não dispara)."""
    c = parse_cron(when)
    t = now.replace(second=0, microsecond=0)
    for _ in range(window_minutes + 1):
        if t.minute in c["minutes"] and t.hour in c["hours"] and day_matches(c, t):
            return t
        t -= timedelta(minutes=1)
    return None


def next_run(when: str, now: datetime, days: int = 400) -> datetime | None:
    c = parse_cron(when)
    hours, minutes = sorted(c["hours"]), sorted(c["minutes"])
    base = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = base.replace(hour=0, minute=0)
    for i in range(days):
        d = day + timedelta(days=i)
        if not day_matches(c, d):
            continue
        for h in hours:
            for m in minutes:
                t = d.replace(hour=h, minute=m)
                if t >= base:
                    return t
    return None


def when_text(when: str) -> str:
    """Como uma pessoa diria a agenda, no idioma da interface; se for exótica, a própria expressão."""
    try:
        c = parse_cron(when)
    except ValueError:
        return when
    mi, hr = when.split()[0], when.split()[1]
    if len(c["minutes"]) == 1 and len(c["hours"]) == 1:
        clock = f"{next(iter(c['hours'])):02d}:{next(iter(c['minutes'])):02d}"
        if c["dom_any"] and c["dow_any"] and len(c["months"]) == 12:
            return T("todo dia às {clock}", clock=clock)
        if c["dom_any"] and len(c["months"]) == 12:
            dow = sorted(c["weekdays"])
            if dow == [1, 2, 3, 4, 5]:
                return T("de segunda a sexta às {clock}", clock=clock)
            if dow == [0, 6]:
                return T("aos sábados e domingos às {clock}", clock=clock)
            if len(dow) == 1:
                d = days_for_display()[dow[0]]
                return T("todo {day} às {clock}", day=d, clock=clock) if dow[0] in (0, 6) else T("toda {day} às {clock}", day=d, clock=clock)
            return T("às {days} às {clock}", days=", ".join(days_for_display()[d] for d in dow), clock=clock)
        if c["dow_any"] and len(c["months"]) == 12 and len(c["month_days"]) == 1:
            return T("todo dia {day} do mês às {clock}", day=next(iter(c["month_days"])), clock=clock)
    if mi.startswith("*/") and hr == "*" and c["dom_any"] and c["dow_any"]:
        return T("a cada {n} minutos", n=mi[2:])
    if mi.isdigit() and hr.startswith("*/") and c["dom_any"] and c["dow_any"]:
        return T("a cada {n} horas", n=hr[2:])
    return when


# onde a rotina publica: a conversa do dono, o primeiro grupo de um assistente ou um canal dele, pelo id
WHERE_RE = re.compile(r"^assistant:[a-z][a-z0-9-]{1,30}(:-?[0-9]{5,25})?$")
# a rotina que publica no grupo de um assistente fala com as pessoas de lá, não com o dono: o cargo precisa saber
PUBLISH_NOTE = ("PUBLICAÇÃO: o texto da sua entrega é publicado tal como está no canal do assistente, para as pessoas "
                "que estão lá. Escreva na entrega a mensagem final, pronta para ser lida por elas, e nada além dela: "
                "sem contar o que você fez, sem avisos para o dono e sem dizer que não enviou. Quem envia é o sistema. "
                "Se faltar algo para escrever a mensagem, peça uma decisão em vez de entregar.")


def validate_routine(r: dict, positions: list[dict]) -> str | None:
    """Mensagem de erro para a pessoa, ou None."""
    if not NAME_RE.match(r.get("name") or ""):
        return T("nome: só letras minúsculas, números e hífen, começando por letra (2 a 31 caracteres)")
    if not (r.get("title") or "").strip():
        return T("a rotina precisa de um título")
    if not (r.get("does") or "").strip():
        return T("diga o que a rotina faz")
    try:
        parse_cron(r.get("when") or "")
    except ValueError as e:
        return T("quando: {error}", error=e)
    where = r.get("where") or "telegram"
    if where != "telegram" and not WHERE_RE.match(where):
        return T("onde: 'telegram', 'assistant:<nome>' ou 'assistant:<nome>:<id do canal>'")
    if positions and r.get("position") not in {c["name"] for c in positions}:
        return T("cargo desconhecido: {position}", position=r.get("position"))
    return None


# ---------------------------------------------------------------- disparo (a tarefa da rotina no Paperclip)
def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(T("Paperclip respondeu HTTP {code}: {detail}", code=e.code, detail=e.read().decode("utf-8", "replace")[:200])) from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(T("o Escritório (Paperclip) não está no ar neste computador")) from e


def as_list(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])


def fire(home: Path, routine: dict, when: datetime | None = None) -> dict:
    """Cria a tarefa desta rotina no cargo certo. A entrega volta por `onde` (o carteiro lê o rodapé [origin: …])."""
    ids = position_ids(home)
    position = ids.get(routine.get("position") or "work") or {}
    if not position.get("id"):
        raise RuntimeError(T("o cargo {position} ainda não está em uso (coloque em uso na página ESCRITÓRIO)", position=routine.get("position")))
    comp = as_list(api("GET", "/companies"), "companies")
    if not comp:
        raise RuntimeError(T("nenhuma empresa no Escritório"))
    marker = (when or datetime.now()).strftime("%d/%m/%Y %H:%M")
    where = routine.get("where") or "telegram"
    desc = (routine.get("does") or "").strip() + (f"\n\n{PUBLISH_NOTE}" if where.startswith("assistant:") else "") + \
        f"\n\n(rotina {routine['name']}, disparada em {marker})\n\n[origin: {where}]\n[routine: {routine['name']}]"
    it = api("POST", f"/companies/{comp[0]['id']}/issues",
             {"title": (routine.get("title") or routine["name"])[:200], "description": desc, "status": "todo", "priority": "medium", "assigneeAgentId": position["id"]})
    return {"id": it.get("id"), "number": it.get("identifier"), "title": it.get("title")}


def routine_of_task(desc: str) -> str:
    m = re.search(r"\[routine:\s*([^\]]+)\]", desc or "")
    return m.group(1).strip() if m else ""


def schedule(home: Path, state: dict, now: datetime | None = None, log=print, dry: bool = False) -> list[str]:
    """Para cada rotina ligada: se o último horário da agenda ainda não foi atendido, cria a tarefa. Lembra em
    estado['rotinas'][nome] = {'ultima': 'YYYY-MM-DDTHH:MM', 'tarefa': numero}. Devolve o que disparou."""
    now = now or datetime.now()
    cat = catalog(home)
    if not cat["enabled"]:
        return []
    done_routines = state.setdefault("routines", {})
    fired = []
    for r in cat["routines"]:
        if not r.get("enabled"):
            continue
        try:
            due = last_scheduled(r["when"], now)
        except ValueError:
            continue
        if due is None:
            continue
        key = due.strftime("%Y-%m-%dT%H:%M")
        reg = done_routines.get(r["name"]) or {}
        if reg.get("last") == key:
            continue
        if reg.get("last") and reg["last"] > key:
            continue  # já atendeu um horário mais novo (relógio mexido)
        if dry:
            log(f"[carteiro] (dry-run) rotina {r['name']} dispararia a tarefa de {key}")
            done_routines[r["name"]] = {"last": key, "task": ""}
            fired.append(r["name"])
            continue
        try:
            t = fire(home, r, due)
        except RuntimeError as e:
            log(f"[carteiro] rotina {r['name']} não disparou: {e}")
            continue
        done_routines[r["name"]] = {"last": key, "task": t.get("number") or "", "at": int(time.time())}
        fired.append(r["name"])
        log(f"[carteiro] rotina {r['name']}: tarefa {t.get('number')} criada")
    return fired
