#!/usr/bin/env python3
"""jarvis_rotinas.py — o que o Escritório sabe sobre cargos e rotinas fora do render: o catálogo em uso, a loja
da página, a agenda (cron de 5 campos) e a criação da tarefa de uma rotina no Paperclip.

Usado pelo carteiro (agenda), pelo jarvis_setup.py (status e comandos da página) e pelo MCP escritorio (lista de
cargos). Só biblioteca padrão. Nunca imprime segredo.

Arquivos:
  $JARVIS_HOME/render/escritorio.json       o que o render aplicou: ligado, cargos (tenant + página), rotinas do tenant
  $JARVIS_HOME/escritorio.json              loja da página: {ligado, cargos: [...]}  (cargos precisam de "colocar em uso")
  $JARVIS_HOME/escritorio/rotinas.json      loja da página: [rotinas]               (valem na hora; o carteiro lê daqui)
  $JARVIS_HOME/escritorio/cargos.json       cargos criados no Paperclip: {nome: {id, titulo}}
  $JARVIS_HOME/escritorio/carteiro.json     estado do carteiro: avisos feitos e última execução de cada rotina
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

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
NOME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
CARGO_PADRAO = {"nome": "trabalho", "titulo": "Assistente de trabalho",
                "faz": "Executa o que o JARVIS e os assistentes delegam: pesquisas, documentos, propostas, resumos."}
ROTINA_PADRAO = {"nome": "resumo-da-semana", "titulo": "Resumo da semana", "quando": "0 8 * * 1", "onde": "telegram", "cargo": "trabalho", "ligada": True,
                 "faz": ("Faça o resumo da semana do Escritório e do second brain: o que foi entregue, o que está pendente ou parado, "
                         "e o que mudou nas páginas do second brain nos últimos sete dias. Em tópicos curtos, em português do Brasil.")}
DIAS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"]


# ---------------------------------------------------------------- arquivos
def ler_json(path: Path, padrao):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def gravar_json(path: Path, dados) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def loja_rotinas(home: Path) -> list[dict]:
    d = ler_json(home / "escritorio" / "rotinas.json", [])
    return [r for r in d if isinstance(r, dict) and r.get("nome")] if isinstance(d, list) else []


def loja_cargos(home: Path) -> list[dict]:
    d = ler_json(home / "escritorio.json", {})
    c = d.get("cargos") if isinstance(d, dict) else None
    return [x for x in c if isinstance(x, dict) and x.get("nome")] if isinstance(c, list) else []


def normaliza_cargo(c: dict) -> dict:
    out = {"nome": str(c.get("nome") or "").strip(), "titulo": str(c.get("titulo") or c.get("nome") or "").strip()[:80],
           "faz": str(c.get("faz") or "").strip()[:600]}
    if c.get("nivel") in ("rapido", "equilibrado", "capaz"):
        out["nivel"] = c["nivel"]  # vazio ou ausente: segue o nível geral de quem pensa
    return out


def normaliza_rotina(r: dict) -> dict:
    return {"nome": str(r.get("nome") or "").strip(), "titulo": str(r.get("titulo") or r.get("nome") or "").strip()[:120],
            "faz": str(r.get("faz") or "").strip()[:2000], "quando": str(r.get("quando") or "").strip(),
            "onde": str(r.get("onde") or "telegram").strip(), "cargo": str(r.get("cargo") or "trabalho").strip(),
            "ligada": bool(r.get("ligada", True))}


def mesclar(base: list[dict], loja: list[dict], normaliza) -> list[dict]:
    """A lista aplicada (tenant) com a loja da página por cima: nome repetido, vale o da página; `removido` esconde."""
    out = [normaliza(x) for x in base if x.get("nome")]
    for item in loja:
        out = [x for x in out if x["nome"] != item["nome"]]
        if item.get("removido"):
            continue
        out.append(normaliza(item))
    return out


def catalogo(home: Path) -> dict:
    """O catálogo em uso: ligado, cargos (aplicados + loja, com `aplicado`) e rotinas (tenant + loja), com o cargo e a rotina padrão."""
    render = ler_json(home / "render" / "escritorio.json", {})
    if not isinstance(render, dict):
        render = {}
    ligado = bool(render.get("ligado"))
    loja = ler_json(home / "escritorio.json", {})
    if isinstance(loja, dict) and "ligado" in loja:
        ligado_desejado = bool(loja["ligado"])
    else:
        ligado_desejado = ligado
    aplicados = {c["nome"] for c in render.get("cargos") or [] if isinstance(c, dict) and c.get("nome")}
    cargos_render = [c for c in render.get("cargos") or [] if isinstance(c, dict)]
    if not any(c.get("nome") == "trabalho" for c in cargos_render):
        cargos_render.insert(0, dict(CARGO_PADRAO))
    cargos = mesclar(cargos_render, loja_cargos(home), normaliza_cargo)
    for c in cargos:
        c["aplicado"] = c["nome"] in aplicados or (c["nome"] == "trabalho" and ligado)
        c["padrao"] = c["nome"] == "trabalho"
    rot_render = [r for r in render.get("rotinas") or [] if isinstance(r, dict)]
    if not any(r.get("nome") == ROTINA_PADRAO["nome"] for r in rot_render):
        rot_render.insert(0, dict(ROTINA_PADRAO))
    rotinas = mesclar(rot_render, loja_rotinas(home), normaliza_rotina)
    for r in rotinas:
        r["padrao"] = r["nome"] == ROTINA_PADRAO["nome"]
    return {"ligado": ligado, "ligado_desejado": ligado_desejado, "cargos": cargos, "rotinas": rotinas}


def cargos_ids(home: Path) -> dict:
    """{nome: {id, titulo}} dos cargos criados no Paperclip. Instalação do P0/P1 (só cargo.json): o trabalho."""
    d = ler_json(home / "escritorio" / "cargos.json", None)
    if isinstance(d, dict):
        return d
    antigo = ler_json(home / "escritorio" / "cargo.json", {})
    if isinstance(antigo, dict) and antigo.get("id"):
        return {"trabalho": {"id": antigo["id"], "titulo": antigo.get("nome") or CARGO_PADRAO["titulo"], "empresa": antigo.get("empresa", "")}}
    return {}


# ---------------------------------------------------------------- cron de 5 campos
def _campo(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for parte in expr.split(","):
        parte = parte.strip()
        passo = 1
        if "/" in parte:
            parte, p = parte.split("/", 1)
            passo = int(p)
            if passo < 1:
                raise ValueError("passo inválido")
        if parte in ("*", ""):
            a, b = lo, hi
        elif "-" in parte:
            a, b = (int(x) for x in parte.split("-", 1))
        else:
            a = b = int(parte)
            if passo > 1:
                b = hi
        if a < lo or b > hi or a > b:
            raise ValueError(f"fora do intervalo {lo}-{hi}: {parte}")
        out.update(range(a, b + 1, passo))
    return out


def analisa(quando: str) -> dict:
    """Minutos, horas, dias do mês, meses e dias da semana (0=domingo) que a expressão aceita."""
    campos = (quando or "").split()
    if len(campos) != 5:
        raise ValueError("a agenda precisa de 5 campos: minuto hora dia mês dia-da-semana")
    mi, ho, dm, me, ds = campos
    dias_semana = {d % 7 for d in _campo(ds, 0, 7)}
    return {"minutos": _campo(mi, 0, 59), "horas": _campo(ho, 0, 23), "dias_mes": _campo(dm, 1, 31), "meses": _campo(me, 1, 12),
            "dias_semana": dias_semana, "dm_livre": dm.strip() == "*", "ds_livre": ds.strip() == "*"}


def dia_bate(c: dict, dt: datetime) -> bool:
    if dt.month not in c["meses"]:
        return False
    dm_ok = dt.day in c["dias_mes"]
    ds_ok = ((dt.weekday() + 1) % 7) in c["dias_semana"]  # python: segunda=0; cron: domingo=0
    if c["dm_livre"] and c["ds_livre"]:
        return True
    if c["dm_livre"]:
        return ds_ok
    if c["ds_livre"]:
        return dm_ok
    return dm_ok or ds_ok  # os dois restritos: vale um OU outro (regra do cron)


def bate(quando: str, dt: datetime) -> bool:
    c = analisa(quando)
    return dt.minute in c["minutos"] and dt.hour in c["horas"] and dia_bate(c, dt)


def ultima_programada(quando: str, agora: datetime, janela_minutos: int = 24 * 60) -> datetime | None:
    """O último horário da agenda até agora, dentro da janela (o Mac pode ter dormido; mais velho que isso não dispara)."""
    c = analisa(quando)
    t = agora.replace(second=0, microsecond=0)
    for _ in range(janela_minutos + 1):
        if t.minute in c["minutos"] and t.hour in c["horas"] and dia_bate(c, t):
            return t
        t -= timedelta(minutes=1)
    return None


def proxima(quando: str, agora: datetime, dias: int = 400) -> datetime | None:
    c = analisa(quando)
    horas, minutos = sorted(c["horas"]), sorted(c["minutos"])
    base = agora.replace(second=0, microsecond=0) + timedelta(minutes=1)
    dia = base.replace(hour=0, minute=0)
    for i in range(dias):
        d = dia + timedelta(days=i)
        if not dia_bate(c, d):
            continue
        for h in horas:
            for m in minutos:
                t = d.replace(hour=h, minute=m)
                if t >= base:
                    return t
    return None


def quando_texto(quando: str) -> str:
    """Como uma pessoa diria a agenda; se for exótica, a própria expressão."""
    try:
        c = analisa(quando)
    except ValueError:
        return quando
    mi, ho = quando.split()[0], quando.split()[1]
    if len(c["minutos"]) == 1 and len(c["horas"]) == 1:
        hora = f"{next(iter(c['horas'])):02d}:{next(iter(c['minutos'])):02d}"
        if c["dm_livre"] and c["ds_livre"] and len(c["meses"]) == 12:
            return f"todo dia às {hora}"
        if c["dm_livre"] and len(c["meses"]) == 12:
            ds = sorted(c["dias_semana"])
            if ds == [1, 2, 3, 4, 5]:
                return f"de segunda a sexta às {hora}"
            if ds == [0, 6]:
                return f"aos sábados e domingos às {hora}"
            if len(ds) == 1:
                d = DIAS[ds[0]]
                return f"todo {d} às {hora}" if d in ("domingo", "sábado") else f"toda {d} às {hora}"
            return "às " + ", ".join(DIAS[d] for d in ds) + f" às {hora}"
        if c["ds_livre"] and len(c["meses"]) == 12 and len(c["dias_mes"]) == 1:
            return f"todo dia {next(iter(c['dias_mes']))} do mês às {hora}"
    if mi.startswith("*/") and ho == "*" and c["dm_livre"] and c["ds_livre"]:
        return f"a cada {mi[2:]} minutos"
    if mi.isdigit() and ho.startswith("*/") and c["dm_livre"] and c["ds_livre"]:
        return f"a cada {ho[2:]} horas"
    return quando


def valida_rotina(r: dict, cargos: list[dict]) -> str | None:
    """Mensagem de erro para a pessoa, ou None."""
    if not NOME_RE.match(r.get("nome") or ""):
        return "nome: só letras minúsculas, números e hífen, começando por letra (2 a 31 caracteres)"
    if not (r.get("titulo") or "").strip():
        return "a rotina precisa de um título"
    if not (r.get("faz") or "").strip():
        return "diga o que a rotina faz"
    try:
        analisa(r.get("quando") or "")
    except ValueError as e:
        return f"quando: {e}"
    onde = r.get("onde") or "telegram"
    if onde != "telegram" and not re.match(r"^assistente:[a-z][a-z0-9-]{1,30}$", onde):
        return "onde: 'telegram' ou 'assistente:<nome>'"
    if cargos and r.get("cargo") not in {c["nome"] for c in cargos}:
        return f"cargo desconhecido: {r.get('cargo')}"
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
        raise RuntimeError(f"Paperclip respondeu HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("o Escritório (Paperclip) não está no ar neste computador") from e


def lista(d, chave):
    return d if isinstance(d, list) else (d or {}).get(chave, [])


def disparar(home: Path, rotina: dict, quando: datetime | None = None) -> dict:
    """Cria a tarefa desta rotina no cargo certo. A entrega volta por `onde` (o carteiro lê o rodapé [origem: …])."""
    ids = cargos_ids(home)
    cargo = ids.get(rotina.get("cargo") or "trabalho") or {}
    if not cargo.get("id"):
        raise RuntimeError(f"o cargo {rotina.get('cargo')} ainda não está em uso (coloque em uso na página ESCRITÓRIO)")
    comp = lista(api("GET", "/companies"), "companies")
    if not comp:
        raise RuntimeError("nenhuma empresa no Escritório")
    marca = (quando or datetime.now()).strftime("%d/%m/%Y %H:%M")
    desc = (rotina.get("faz") or "").strip() + f"\n\n(rotina {rotina['nome']}, disparada em {marca})\n\n[origem: {rotina.get('onde') or 'telegram'}]\n[rotina: {rotina['nome']}]"
    it = api("POST", f"/companies/{comp[0]['id']}/issues",
             {"title": (rotina.get("titulo") or rotina["nome"])[:200], "description": desc, "status": "todo", "priority": "medium", "assigneeAgentId": cargo["id"]})
    return {"id": it.get("id"), "numero": it.get("identifier"), "titulo": it.get("title")}


def rotina_de_tarefa(desc: str) -> str:
    m = re.search(r"\[rotina:\s*([^\]]+)\]", desc or "")
    return m.group(1).strip() if m else ""


def agendar(home: Path, estado: dict, agora: datetime | None = None, log=print, dry: bool = False) -> list[str]:
    """Para cada rotina ligada: se o último horário da agenda ainda não foi atendido, cria a tarefa. Lembra em
    estado['rotinas'][nome] = {'ultima': 'YYYY-MM-DDTHH:MM', 'tarefa': numero}. Devolve o que disparou."""
    agora = agora or datetime.now()
    cat = catalogo(home)
    if not cat["ligado"]:
        return []
    feitas = estado.setdefault("rotinas", {})
    disparadas = []
    for r in cat["rotinas"]:
        if not r.get("ligada"):
            continue
        try:
            devido = ultima_programada(r["quando"], agora)
        except ValueError:
            continue
        if devido is None:
            continue
        chave = devido.strftime("%Y-%m-%dT%H:%M")
        reg = feitas.get(r["nome"]) or {}
        if reg.get("ultima") == chave:
            continue
        if reg.get("ultima") and reg["ultima"] > chave:
            continue  # já atendeu um horário mais novo (relógio mexido)
        if dry:
            log(f"[carteiro] (dry-run) rotina {r['nome']} dispararia a tarefa de {chave}")
            feitas[r["nome"]] = {"ultima": chave, "tarefa": ""}
            disparadas.append(r["nome"])
            continue
        try:
            t = disparar(home, r, devido)
        except RuntimeError as e:
            log(f"[carteiro] rotina {r['nome']} não disparou: {e}")
            continue
        feitas[r["nome"]] = {"ultima": chave, "tarefa": t.get("numero") or "", "em": int(time.time())}
        disparadas.append(r["nome"])
        log(f"[carteiro] rotina {r['nome']}: tarefa {t.get('numero')} criada")
    return disparadas
