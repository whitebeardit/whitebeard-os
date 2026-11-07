#!/usr/bin/env python3
"""jarvis_escritorio_tokens.py — onde os tokens do Escritório vão. Lê as sessões que o Hermes guarda no perfil de
cada cargo (state.db, só leitura) e as execuções do Paperclip (qual tarefa cada execução atendeu), casa as duas pelo
horário e devolve: total do mês, por cargo (com o mês anterior), por rotina e as tarefas mais caras; e os sinais
(rotina muito acima da média dela, tarefa acima do teto, cargo que dobrou, tarefa presa, cargo falhando).

A conta é por assinatura: o que importa é o uso, não o dólar. "Tokens" aqui = entrada + saída + cache lido + cache
escrito (tudo o que o modelo processou). Só biblioteca padrão. Nunca escreve nada.

Uso (teste): jarvis_escritorio_tokens.py --home <JARVIS_HOME>
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

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
TETO_TAREFA = 500_000          # tokens numa tarefa só: acima disto, sinal
FATOR_ROTINA = 3.0             # última execução da rotina > 3× a média das anteriores
FATOR_CARGO = 2.0              # cargo gastou mais que o dobro do mês anterior
HORAS_PRESA = 6                # tarefa na fila/em andamento sem mudar há mais de 6 h
FALHAS_SEGUIDAS = 3            # execuções falhadas em sequência no cargo
FOLGA_S = 90                   # folga para casar sessão e execução pelo horário


def api(path: str):
    req = urllib.request.Request(API + path, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def lista(d, chave):
    return d if isinstance(d, list) else (d or {}).get(chave, [])


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


def sessoes_do_perfil(hermes_home: Path, perfil: str, desde: float) -> list[dict]:
    """As sessões do cargo desde `desde` (epoch), com os tokens. Banco aberto só para leitura; sem banco, lista vazia."""
    db = hermes_home / "profiles" / perfil / "state.db"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "select id, started_at, ended_at, api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, model "
                "from sessions where started_at >= ? order by started_at", (desde,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        ent, sai, cl, ce = (int(x or 0) for x in r[4:8])
        out.append({"id": r[0], "inicio": float(r[1] or 0), "fim": float(r[2] or 0), "chamadas": int(r[3] or 0),
                    "entrada": ent, "saida": sai, "cache_lido": cl, "cache_escrito": ce, "total": ent + sai + cl + ce, "modelo": r[8] or ""})
    return out


def soma(itens: list[dict]) -> dict:
    out = {"entrada": 0, "saida": 0, "cache_lido": 0, "cache_escrito": 0, "total": 0, "execucoes": 0}
    for s in itens:
        for k in ("entrada", "saida", "cache_lido", "cache_escrito", "total"):
            out[k] += s.get(k, 0)
        out["execucoes"] += 1
    return out


def calcular(home: Path, cargos: list[dict], ids: dict, issues: list[dict] | None = None, runs: list[dict] | None = None, agora: float | None = None) -> dict:
    """cargos: catálogo ({nome, titulo}); ids: {nome: {id}} (agente do Paperclip). issues/runs: injetáveis nos testes."""
    agora = agora or time.time()
    hermes_home = Path(read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes"))
    hoje = datetime.fromtimestamp(agora)
    inicio_mes = datetime(hoje.year, hoje.month, 1).timestamp()
    mes_ant = datetime(hoje.year - 1, 12, 1) if hoje.month == 1 else datetime(hoje.year, hoje.month - 1, 1)
    inicio_ant = mes_ant.timestamp()
    vazio = {"mes": soma([]), "cargos": [], "rotinas": [], "tarefas": [], "alertas": [], "desde": datetime.fromtimestamp(inicio_ant).strftime("%Y-%m-%d")}
    try:
        if issues is None or runs is None:
            comp = lista(api("/companies"), "companies")
            if not comp:
                return vazio
            cid = comp[0]["id"]
            issues = issues if issues is not None else lista(api(f"/companies/{cid}/issues"), "issues")
            runs = runs if runs is not None else lista(api(f"/companies/{cid}/heartbeat-runs?limit=500"), "runs")
    except Exception:
        return vazio
    por_id = {it["id"]: it for it in issues}
    agente_cargo = {(ids.get(c["nome"]) or {}).get("id"): c for c in cargos if (ids.get(c["nome"]) or {}).get("id")}
    # execuções por cargo, com a tarefa que atenderam
    exec_cargo: dict[str, list[dict]] = {c["nome"]: [] for c in cargos}
    for r in runs:
        c = agente_cargo.get(r.get("agentId"))
        if not c:
            continue
        ini = ts(r.get("startedAt") or r.get("createdAt") or "")
        if ini < inicio_ant - 86400:
            continue
        ctx = r.get("contextSnapshot") or {}
        exec_cargo[c["nome"]].append({"inicio": ini, "fim": ts(r.get("finishedAt") or "") or ini + 3600, "issue": ctx.get("issueId") or "",
                                      "status": r.get("status") or "", "cargo": c["nome"]})
    # cada sessão do perfil vai para a execução cujo intervalo a contém (com folga); sobra = sem tarefa
    por_tarefa: dict[str, list[dict]] = {}
    por_cargo_mes: dict[str, list[dict]] = {}
    por_cargo_ant: dict[str, list[dict]] = {}
    for c in cargos:
        execs = sorted(exec_cargo[c["nome"]], key=lambda e: e["inicio"])
        for s in sessoes_do_perfil(hermes_home, c["nome"], inicio_ant - 86400):
            alvo = next((e for e in execs if e["inicio"] - FOLGA_S <= s["inicio"] <= e["fim"] + FOLGA_S), None)
            s = {**s, "cargo": c["nome"], "issue": alvo["issue"] if alvo else ""}
            (por_cargo_mes if s["inicio"] >= inicio_mes else por_cargo_ant).setdefault(c["nome"], []).append(s)
            if alvo and alvo["issue"]:
                por_tarefa.setdefault(alvo["issue"], []).append(s)
    sess_mes = [s for v in por_cargo_mes.values() for s in v]
    out = {"mes": soma(sess_mes), "desde": datetime.fromtimestamp(inicio_mes).strftime("%Y-%m-%d"), "cargos": [], "rotinas": [], "tarefas": [], "alertas": []}
    for c in cargos:
        m, a = soma(por_cargo_mes.get(c["nome"], [])), soma(por_cargo_ant.get(c["nome"], []))
        out["cargos"].append({"nome": c["nome"], "titulo": c.get("titulo") or c["nome"], **m, "mes_anterior": a["total"]})
    # tarefas (todas as que têm sessão), com rotina e cargo
    tarefas = []
    for iid, ss in por_tarefa.items():
        it = por_id.get(iid) or {"title": "(tarefa apagada)"}
        desc = it.get("description") or ""
        m = re.search(r"\[rotina:\s*([^\]]+)\]", desc)
        tarefas.append({"id": iid, "numero": it.get("identifier") or iid[:8], "titulo": it.get("title") or "", "cargo": ss[0]["cargo"],
                        "rotina": m.group(1).strip() if m else "", **soma(ss), "quando": datetime.fromtimestamp(max(s["inicio"] for s in ss)).strftime("%Y-%m-%dT%H:%M"),
                        "no_mes": max(s["inicio"] for s in ss) >= inicio_mes})
    tarefas.sort(key=lambda t: t["total"], reverse=True)
    out["tarefas"] = [t for t in tarefas if t["no_mes"]][:10]
    # rotinas: média das execuções anteriores × a última
    por_rotina: dict[str, list[dict]] = {}
    for t in sorted(tarefas, key=lambda t: t["quando"]):
        if t["rotina"]:
            por_rotina.setdefault(t["rotina"], []).append(t)
    for nome, ts_ in por_rotina.items():
        no_mes = [t for t in ts_ if t["no_mes"]]
        media = sum(t["total"] for t in ts_) / len(ts_)
        out["rotinas"].append({"nome": nome, "titulo": ts_[-1]["titulo"], "total": sum(t["total"] for t in no_mes), "execucoes": len(no_mes),
                               "media": int(media), "ultima": ts_[-1]["total"], "ultima_tarefa": ts_[-1]["numero"]})
        anteriores = ts_[:-1]
        if len(anteriores) >= 2:
            media_ant = sum(t["total"] for t in anteriores) / len(anteriores)
            if media_ant > 0 and ts_[-1]["total"] > FATOR_ROTINA * media_ant:
                out["alertas"].append({"tipo": "rotina-cara", "nivel": "warn", "rotina": nome, "tarefa": ts_[-1]["numero"],
                                       "texto": f"A rotina {ts_[-1]['titulo']} gastou {fmt(ts_[-1]['total'])} tokens na última vez, {ts_[-1]['total'] / media_ant:.0f}× a média dela.",
                                       "dica": "Encurte o pedido, restrinja a área do second brain que ela lê ou reduza a frequência."})
    # sinais
    for t in out["tarefas"]:
        if t["total"] > TETO_TAREFA:
            out["alertas"].append({"tipo": "tarefa-cara", "nivel": "warn", "tarefa": t["numero"],
                                   "texto": f"A tarefa {t['numero']} ({t['titulo'][:40]}) passou de {fmt(TETO_TAREFA)} tokens: {fmt(t['total'])}.",
                                   "dica": "Pedidos grandes rendem mais em partes; veja se o cargo releu o second brain inteiro a cada passo."})
    for c in out["cargos"]:
        if c["mes_anterior"] >= 100_000 and c["total"] > FATOR_CARGO * c["mes_anterior"]:
            out["alertas"].append({"tipo": "cargo-dobrou", "nivel": "warn", "cargo": c["nome"],
                                   "texto": f"O cargo {c['titulo']} já gastou {fmt(c['total'])} tokens este mês, mais que o dobro do mês passado ({fmt(c['mes_anterior'])}).",
                                   "dica": "Veja as tarefas mais caras dele abaixo; uma rotina nova costuma ser a causa."})
    for it in issues:
        if it.get("status") in ("todo", "in_progress") and it.get("assigneeAgentId") in agente_cargo:
            parada = agora - ts(it.get("updatedAt") or it.get("createdAt") or "")
            if parada > HORAS_PRESA * 3600:
                c = agente_cargo[it["assigneeAgentId"]]
                out["alertas"].append({"tipo": "tarefa-presa", "nivel": "alert", "tarefa": it.get("identifier") or "",
                                       "texto": f"A tarefa {it.get('identifier')} ({(it.get('title') or '')[:40]}) está parada há {int(parada // 3600)} h com o cargo {c.get('titulo') or c['nome']}.",
                                       "dica": "Veja o registro do Paperclip em DIAGNÓSTICOS; se o cargo travou, recuse a tarefa e peça de novo."})
    for c in cargos:
        execs = sorted(exec_cargo[c["nome"]], key=lambda e: e["inicio"])[-FALHAS_SEGUIDAS:]
        if len(execs) == FALHAS_SEGUIDAS and all(e["status"] in ("failed", "timed_out", "cancelled") for e in execs):
            out["alertas"].append({"tipo": "cargo-falhando", "nivel": "alert", "cargo": c["nome"],
                                   "texto": f"O cargo {c.get('titulo') or c['nome']} falhou nas últimas {FALHAS_SEGUIDAS} execuções.",
                                   "dica": "Veja o registro do Paperclip em DIAGNÓSTICOS; costuma ser o Hermes sem modelo ou o Escritório fora do ar."})
    return out


def com_cache(home: Path, cargos: list[dict], ids: dict, ttl: int = 60) -> dict:
    """O cálculo lê o banco do Hermes e as execuções do Paperclip: a página pergunta a cada poucos segundos, então o
    resultado fica um minuto em escritorio/tokens.json."""
    cache = home / "escritorio" / "tokens.json"
    try:
        if time.time() - cache.stat().st_mtime < ttl:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = calcular(home, cargos, ids)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def fmt(n: int) -> str:
    n = int(n or 0)
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
    import jarvis_rotinas as rot
    cat = rot.catalogo(home)
    print(json.dumps(calcular(home, cat["cargos"], rot.cargos_ids(home)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
