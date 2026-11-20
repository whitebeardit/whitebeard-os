#!/usr/bin/env python3
"""jarvis_office_tokens.py — onde os tokens do Escritório vão. Lê as sessões que o Hermes guarda no perfil de
cada cargo (state.db, só leitura) e as execuções do Paperclip (qual tarefa cada execução atendeu), casa as duas pelo
horário e devolve: total do mês, por cargo (com o mês anterior), por rotina e as tarefas mais caras; e os sinais
(rotina muito acima da média dela, tarefa acima do teto, cargo que dobrou, tarefa presa, cargo falhando).

A conta é por assinatura: o que importa é o uso, não o dólar. "Tokens" aqui = entrada + saída + cache lido + cache
escrito (tudo o que o modelo processou). Só biblioteca padrão. Nunca escreve nada.

Uso (teste): jarvis_office_tokens.py --home <JARVIS_HOME>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from jarvis_i18n import T, language

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
TASK_LIMIT = 500_000          # tokens numa tarefa só: acima disto, sinal
ROUTINE_FACTOR = 3.0             # última execução da rotina > 3× a média das anteriores
POSITION_FACTOR = 2.0              # cargo gastou mais que o dobro do mês anterior
STUCK_HOURS = 6                # tarefa na fila/em andamento sem mudar há mais de 6 h
FAILURES_IN_A_ROW = 3            # execuções falhadas em sequência no cargo
SLACK_S = 90                   # folga para casar sessão e execução pelo horário


def api(path: str):
    req = urllib.request.Request(API + path, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def as_list(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])


def ts(iso: str) -> float:
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


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


def profile_sessions(hermes_home: Path, profile: str, since: float) -> list[dict]:
    """As sessões do cargo desde `desde` (epoch), com os tokens. Banco aberto só para leitura; sem banco, lista vazia."""
    db = hermes_home / "profiles" / profile / "state.db"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "select id, started_at, ended_at, api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, model "
                "from sessions where started_at >= ? order by started_at", (since,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        inp, outp, cl, ce = (int(x or 0) for x in r[4:8])
        out.append({"id": r[0], "started_at": float(r[1] or 0), "finished_at": float(r[2] or 0), "calls": int(r[3] or 0),
                    "input": inp, "output": outp, "cache_read": cl, "cache_write": ce, "total": inp + outp + cl + ce, "model": r[8] or ""})
    return out


def total_of(items: list[dict]) -> dict:
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0, "runs": 0}
    for s in items:
        for k in ("input", "output", "cache_read", "cache_write", "total"):
            out[k] += s.get(k, 0)
        out["runs"] += 1
    return out


def compute(home: Path, positions: list[dict], ids: dict, issues: list[dict] | None = None, runs: list[dict] | None = None, now: float | None = None) -> dict:
    """cargos: catálogo ({nome, titulo}); ids: {nome: {id}} (agente do Paperclip). issues/runs: injetáveis nos testes."""
    now = now or time.time()
    hermes_home = Path(read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes"))
    today = datetime.fromtimestamp(now)
    month_start = datetime(today.year, today.month, 1).timestamp()
    previous_month = datetime(today.year - 1, 12, 1) if today.month == 1 else datetime(today.year, today.month - 1, 1)
    previous_start = previous_month.timestamp()
    empty = {"month": total_of([]), "positions": [], "routines": [], "tasks": [], "alerts": [], "since": datetime.fromtimestamp(previous_start).strftime("%Y-%m-%d")}
    try:
        if issues is None or runs is None:
            comp = as_list(api("/companies"), "companies")
            if not comp:
                return empty
            cid = comp[0]["id"]
            issues = issues if issues is not None else as_list(api(f"/companies/{cid}/issues"), "issues")
            runs = runs if runs is not None else as_list(api(f"/companies/{cid}/heartbeat-runs?limit=500"), "runs")
    except Exception:
        return empty
    by_id = {it["id"]: it for it in issues}
    agent_position = {(ids.get(c["name"]) or {}).get("id"): c for c in positions if (ids.get(c["name"]) or {}).get("id")}
    # execuções por cargo, com a tarefa que atenderam
    runs_by_position: dict[str, list[dict]] = {c["name"]: [] for c in positions}
    for r in runs:
        c = agent_position.get(r.get("agentId"))
        if not c:
            continue
        start = ts(r.get("startedAt") or r.get("createdAt") or "")
        if start < previous_start - 86400:
            continue
        ctx = r.get("contextSnapshot") or {}
        runs_by_position[c["name"]].append({"started_at": start, "finished_at": ts(r.get("finishedAt") or "") or start + 3600, "issue": ctx.get("issueId") or "",
                                      "status": r.get("status") or "", "position": c["name"]})
    # cada sessão do perfil vai para a execução cujo intervalo a contém (com folga); sobra = sem tarefa
    by_task: dict[str, list[dict]] = {}
    month_by_position: dict[str, list[dict]] = {}
    previous_by_position: dict[str, list[dict]] = {}
    for c in positions:
        execs = sorted(runs_by_position[c["name"]], key=lambda e: e["started_at"])
        for s in profile_sessions(hermes_home, c["name"], previous_start - 86400):
            target = next((e for e in execs if e["started_at"] - SLACK_S <= s["started_at"] <= e["finished_at"] + SLACK_S), None)
            s = {**s, "position": c["name"], "issue": target["issue"] if target else ""}
            (month_by_position if s["started_at"] >= month_start else previous_by_position).setdefault(c["name"], []).append(s)
            if target and target["issue"]:
                by_task.setdefault(target["issue"], []).append(s)
    month_sessions = [s for v in month_by_position.values() for s in v]
    out = {"month": total_of(month_sessions), "since": datetime.fromtimestamp(month_start).strftime("%Y-%m-%d"), "positions": [], "routines": [], "tasks": [], "alerts": [],
           "providers": []}
    # por assinatura (cada uma tem o seu teto): pelo nome do modelo da sessão
    by_provider: dict[str, list[dict]] = {}
    for s_ in month_sessions:
        m = (s_.get("model") or "").lower()
        # os modelos do OpenRouter vêm como fornecedor/modelo (ou openrouter/free)
        prov = "openrouter" if "/" in m else "claude" if m.startswith("claude") else "chatgpt" if (m.startswith("gpt") or m.startswith("o3") or m.startswith("o4") or "codex" in m) else "other"
        by_provider.setdefault(prov, []).append(s_)
    provider_names = {"claude": "Claude", "chatgpt": "ChatGPT", "openrouter": "OpenRouter", "other": T("Outro")}
    out["providers"] = sorted(({"name": k, "title": provider_names[k], **total_of(v)} for k, v in by_provider.items()), key=lambda x: -x["total"])
    out["providers"] = [x for x in out["providers"] if x["total"] > 0]
    for c in positions:
        m, a = total_of(month_by_position.get(c["name"], [])), total_of(previous_by_position.get(c["name"], []))
        out["positions"].append({"name": c["name"], "title": c.get("title") or c["name"], **m, "previous_month": a["total"]})
    # tarefas (todas as que têm sessão), com rotina e cargo
    tasks = []
    for iid, ss in by_task.items():
        it = by_id.get(iid) or {"title": T("(tarefa apagada)")}
        desc = it.get("description") or ""
        m = re.search(r"\[routine:\s*([^\]]+)\]", desc)
        tasks.append({"id": iid, "number": it.get("identifier") or iid[:8], "title": it.get("title") or "", "position": ss[0]["position"],
                        "routine": m.group(1).strip() if m else "", **total_of(ss), "when": datetime.fromtimestamp(max(s["started_at"] for s in ss)).strftime("%Y-%m-%dT%H:%M"),
                        "in_month": max(s["started_at"] for s in ss) >= month_start})
    tasks.sort(key=lambda t: t["total"], reverse=True)
    out["tasks"] = [t for t in tasks if t["in_month"]][:10]
    # rotinas: média das execuções anteriores × a última
    by_routine: dict[str, list[dict]] = {}
    for t in sorted(tasks, key=lambda t: t["when"]):
        if t["routine"]:
            by_routine.setdefault(t["routine"], []).append(t)
    for name, ts_ in by_routine.items():
        in_month = [t for t in ts_ if t["in_month"]]
        average = sum(t["total"] for t in ts_) / len(ts_)
        out["routines"].append({"name": name, "title": ts_[-1]["title"], "total": sum(t["total"] for t in in_month), "runs": len(in_month),
                               "average": int(average), "last": ts_[-1]["total"], "last_task": ts_[-1]["number"]})
        previous_runs = ts_[:-1]
        if len(previous_runs) >= 2:
            previous_average = sum(t["total"] for t in previous_runs) / len(previous_runs)
            if previous_average > 0 and ts_[-1]["total"] > ROUTINE_FACTOR * previous_average:
                out["alerts"].append({"type": "costly-routine", "level": "warn", "routine": name, "task": ts_[-1]["number"],
                                       "text": T("A rotina {title} gastou {tokens} tokens na última vez, {times}× a média dela.", title=ts_[-1]["title"], tokens=fmt(ts_[-1]["total"]), times=f"{ts_[-1]['total'] / previous_average:.0f}"),
                                       "hint": T("Encurte o pedido, restrinja a área do second brain que ela lê ou reduza a frequência.")})
    # sinais
    for t in out["tasks"]:
        if t["total"] > TASK_LIMIT:
            out["alerts"].append({"type": "costly-task", "level": "warn", "task": t["number"],
                                   "text": T("A tarefa {number} ({title}) passou de {limit} tokens: {tokens}.", number=t["number"], title=t["title"][:40], limit=fmt(TASK_LIMIT), tokens=fmt(t["total"])),
                                   "hint": T("Pedidos grandes rendem mais em partes; veja se o cargo releu o second brain inteiro a cada passo.")})
    for c in out["positions"]:
        if c["previous_month"] >= 100_000 and c["total"] > POSITION_FACTOR * c["previous_month"]:
            out["alerts"].append({"type": "position-doubled", "level": "warn", "position": c["name"],
                                   "text": T("O cargo {title} já gastou {tokens} tokens este mês, mais que o dobro do mês passado ({previous}).", title=c["title"], tokens=fmt(c["total"]), previous=fmt(c["previous_month"])),
                                   "hint": T("Veja as tarefas mais caras dele abaixo; uma rotina nova costuma ser a causa.")})
    for it in issues:
        if it.get("status") in ("todo", "in_progress") and it.get("assigneeAgentId") in agent_position:
            idle = now - ts(it.get("updatedAt") or it.get("createdAt") or "")
            if idle > STUCK_HOURS * 3600:
                c = agent_position[it["assigneeAgentId"]]
                out["alerts"].append({"type": "stuck-task", "level": "alert", "task": it.get("identifier") or "",
                                       "text": T("A tarefa {number} ({title}) está parada há {hours} h com o cargo {position}.", number=it.get("identifier"), title=(it.get("title") or "")[:40], hours=int(idle // 3600), position=c.get("title") or c["name"]),
                                       "hint": T("Veja o registro do Paperclip em DIAGNÓSTICOS; se o cargo travou, recuse a tarefa e peça de novo.")})
    for c in positions:
        execs = sorted(runs_by_position[c["name"]], key=lambda e: e["started_at"])[-FAILURES_IN_A_ROW:]
        if len(execs) == FAILURES_IN_A_ROW and all(e["status"] in ("failed", "timed_out", "cancelled") for e in execs):
            out["alerts"].append({"type": "position-failing", "level": "alert", "position": c["name"],
                                   "text": T("O cargo {position} falhou nas últimas {n} execuções.", position=c.get("title") or c["name"], n=FAILURES_IN_A_ROW),
                                   "hint": T("Veja o registro do Paperclip em DIAGNÓSTICOS; costuma ser o Hermes sem modelo ou o Escritório fora do ar.")})
    return out


def with_cache(home: Path, positions: list[dict], ids: dict, ttl: int = 60) -> dict:
    """O cálculo lê o banco do Hermes e as execuções do Paperclip: a página pergunta a cada poucos segundos, então o
    resultado fica um minuto em office/tokens.json. Os alertas levam texto no idioma da interface: o inglês tem o
    seu próprio arquivo (tokens.en.json), para a troca de idioma não mostrar o texto guardado no outro."""
    cache = home / "office" / ("tokens.json" if language() == "pt-BR" else "tokens.en.json")
    try:
        if time.time() - cache.stat().st_mtime < ttl:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = compute(home, positions, ids)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def fmt(n: int) -> str:
    n = int(n or 0)
    if language() == "en":
        return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n // 1000}k" if n >= 1_000 else str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{n // 1000} mil"
    return str(n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    a = ap.parse_args()
    home = Path(a.home).expanduser()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import jarvis_routines as routines_lib
    cat = routines_lib.catalog(home)
    print(json.dumps(compute(home, cat["positions"], routines_lib.position_ids(home)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
