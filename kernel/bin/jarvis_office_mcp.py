#!/usr/bin/env python3
"""jarvis_office_mcp.py — servidor MCP (stdio) do Escritório: as ferramentas com que os assistentes e os
cargos falam com o Paperclip sem terminal.

Para quem pede (o JARVIS do dono, assistentes da equipe): create_task, my_tasks, approve, reject.
Para quem executa (os cargos, dentro de um heartbeat do Paperclip): deliver, request_approval, comment.

Ambiente: PAPERCLIP_API_URL (padrão http://127.0.0.1:3100/api), PAPERCLIP_API_KEY (opcional: no modo local
não há chave), PAPERCLIP_RUN_ID (dentro de um heartbeat), OFFICE_POSITION_ID (cargo padrão das tarefas novas),
OFFICE_ORIGIN (para onde o carteiro avisa: 'telegram', 'telegram:<chat>', 'discord:#canal'…).
Só biblioteca padrão. Nunca escreve segredo na saída.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from jarvis_i18n import T

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
if not API.endswith("/api"):
    API += "/api"
API_KEY = os.environ.get("PAPERCLIP_API_KEY", "")
RUN_ID = os.environ.get("PAPERCLIP_RUN_ID", "")
POSITION = os.environ.get("OFFICE_POSITION_ID", "")
POSITION_NAME = os.environ.get("OFFICE_POSITION_NAME", "Assistente de trabalho")
ORIGIN = os.environ.get("OFFICE_ORIGIN", "telegram")
COMPANY = os.environ.get("PAPERCLIP_COMPANY_ID", "")
JARVIS_HOME = os.environ.get("JARVIS_HOME", "")


def positions_catalog() -> list[dict]:
    """Os cargos em uso neste computador: nome, título, o que faz e o id do agente (de $JARVIS_HOME/office/positions.json)."""
    if not JARVIS_HOME:
        return []
    try:
        ids = json.loads(open(os.path.join(JARVIS_HOME, "office", "positions.json"), encoding="utf-8").read())
    except (OSError, ValueError):
        ids = {}
    try:
        render = json.loads(open(os.path.join(JARVIS_HOME, "render", "office.json"), encoding="utf-8").read())
        positions = [c for c in (render.get("positions") or []) if isinstance(c, dict) and c.get("name")]
    except (OSError, ValueError, AttributeError):
        positions = []
    if not any(c.get("name") == "work" for c in positions):
        positions.insert(0, {"name": "work", "title": POSITION_NAME, "does": "pesquisas, documentos, propostas, resumos"})
    out = []
    for c in positions:
        reg = ids.get(c["name"]) if isinstance(ids, dict) else None
        out.append({"name": c["name"], "title": c.get("title") or c["name"], "does": c.get("does") or "", "id": (reg or {}).get("id", "")})
    return out


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    if RUN_ID and method in ("POST", "PATCH", "DELETE"):
        headers["X-Paperclip-Run-Id"] = RUN_ID
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(T("Paperclip respondeu HTTP {code}: {detail}", code=e.code, detail=detail)) from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(T("o Escritório (Paperclip) não está no ar neste computador")) from e


def as_list(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])


def company_id() -> str:
    global COMPANY
    if COMPANY:
        return COMPANY
    if API_KEY:
        # Dentro de um heartbeat o ator é um agente: GET /companies exige board (403), /agents/me não.
        COMPANY = (api("GET", "/agents/me") or {}).get("companyId") or ""
        if COMPANY:
            return COMPANY
    comp = as_list(api("GET", "/companies"), "companies")
    if not comp:
        raise RuntimeError(T("nenhuma empresa no Escritório"))
    COMPANY = comp[0]["id"]
    return COMPANY


def resolve_task(ref: str) -> dict:
    """Aceita o id, o identificador (PES-12) ou parte do título."""
    ref = (ref or "").strip()
    if not ref:
        raise RuntimeError(T("diga qual tarefa"))
    try:
        return api("GET", f"/issues/{ref}")
    except RuntimeError:
        pass
    for it in as_list(api("GET", f"/companies/{company_id()}/issues"), "issues"):
        if it.get("identifier", "").lower() == ref.lower() or ref.lower() in (it.get("title") or "").lower():
            return it
    raise RuntimeError(T("tarefa não encontrada: {ref}", ref=ref))


def task_summary(it: dict) -> dict:
    return {"id": it.get("id"), "number": it.get("identifier"), "title": it.get("title"), "state": it.get("status"),
            "updated_at": it.get("updatedAt")}


# ---------------------------------------------------------------- ferramentas
def position_id() -> str:
    global POSITION
    if POSITION:
        return POSITION
    for ag in as_list(api("GET", f"/companies/{company_id()}/agents"), "agents"):
        if ag.get("name") == POSITION_NAME and ag.get("status") != "terminated":
            POSITION = ag["id"]
            return POSITION
    raise RuntimeError("o Escritório não tem o cargo 'Assistente de trabalho' neste computador (coloque em uso)")


def chosen_position(position: str) -> tuple[str, str]:
    """(id do agente, título) do cargo pedido, pelo nome ou pelo título; vazio ou desconhecido: o cargo padrão."""
    ref = (position or "").strip().lower()
    cat = positions_catalog()
    if ref:
        for c in cat:
            if ref in (c["name"].lower(), c["title"].lower()) and c.get("id"):
                return c["id"], c["title"]
    default = next((c for c in cat if c["name"] == "work" and c.get("id")), None)
    if default:
        return default["id"], default["title"]
    return position_id(), POSITION_NAME


def create_task(title: str, request: str, origin: str = "", position: str = "") -> dict:
    if not title.strip():
        raise RuntimeError("a tarefa precisa de um título")
    agent, position_title = chosen_position(position)
    desc = (request or "").strip() + f"\n\n[origin: {origin.strip() or ORIGIN}]"
    it = api("POST", f"/companies/{company_id()}/issues",
             {"title": title.strip()[:200], "description": desc, "status": "todo", "priority": "medium", "assigneeAgentId": agent})
    return {"ok": True, "task": task_summary(it), "position": position_title, "message": f"Tarefa {it.get('identifier')} anotada com o cargo {position_title}. Aviso quando ficar pronta."}


def my_tasks(state: str = "") -> dict:
    items = as_list(api("GET", f"/companies/{company_id()}/issues"), "issues")
    if state:
        items = [i for i in items if i.get("status") == state]
    items.sort(key=lambda i: i.get("updatedAt") or "", reverse=True)
    return {"ok": True, "tasks": [task_summary(i) for i in items[:20]]}


def comment(task: str, text: str) -> dict:
    it = resolve_task(task)
    api("POST", f"/issues/{it['id']}/comments", {"body": text})
    return {"ok": True, "task": task_summary(it)}


def change_state(task: str, state: str, comment_text: str) -> dict:
    it = resolve_task(task)
    if RUN_ID:
        # dentro de um heartbeat: uma escrita só, atribuída ao agente (status + comentário), que não o acorda de novo
        updated = api("PATCH", f"/issues/{it['id']}", {"status": state, "comment": comment_text})
    else:
        api("POST", f"/issues/{it['id']}/comments", {"body": comment_text})
        updated = api("PATCH", f"/issues/{it['id']}", {"status": state})
    return {"ok": True, "task": task_summary(updated or it)}


def deliver(task: str, summary: str, path: str = "") -> dict:
    text = "DELIVERY: " + summary.strip() + (f"\nArquivo: {path.strip()}" if path.strip() else "")
    return change_state(task, "done", text)


def pending_of(issue_id: str) -> list[dict]:
    """Interações de confirmação ainda abertas na tarefa (as pendências do dono)."""
    its = as_list(api("GET", f"/issues/{issue_id}/interactions"), "interactions")
    return [i for i in its if i.get("kind") == "request_confirmation" and i.get("status") in ("pending", "open", "awaiting")]


def request_approval(task: str, question: str) -> dict:
    """Cargo: cria a pendência (interação de confirmação do Paperclip) e a tarefa fica bloqueada até a decisão.
    Com continuationPolicy wake_assignee, o cargo acorda sozinho depois que o dono responde."""
    it = resolve_task(task)
    api("POST", f"/issues/{it['id']}/interactions", {
        "kind": "request_confirmation", "title": "Precisa da sua decisão", "continuationPolicy": "wake_assignee",
        "payload": {"version": 1, "prompt": question.strip()[:1000], "acceptLabel": "Aprovar", "rejectLabel": "Recusar"}})
    try:
        api("PATCH", f"/issues/{it['id']}", {"status": "blocked", "comment": "PENDING: " + question.strip()})
    except RuntimeError:
        api("POST", f"/issues/{it['id']}/comments", {"body": "PENDING: " + question.strip()})
    return {"ok": True, "task": task_summary(it), "message": "Pendência registrada; o dono foi avisado. Pare por aqui: você será acordado com a resposta."}


def approve(task: str, note: str = "") -> dict:
    it = resolve_task(task)
    pending = pending_of(it["id"])
    if pending:
        # a nota vai para a descrição (não acorda o cargo); o aceite da pendência é o único despertar, senão
        # comentário + aceite viram duas execuções paralelas e duas entregas
        if note.strip():
            api("PATCH", f"/issues/{it['id']}", {"description": (it.get("description") or "") + f"\n\nNota do dono ao aprovar: {note.strip()}"})
        for p_ in pending:
            api("POST", f"/issues/{it['id']}/interactions/{p_['id']}/accept", {})
        return {"ok": True, "task": task_summary(it), "message": "Aprovado; o cargo retoma o trabalho."}
    return change_state(task, "todo", "APPROVED" + (f": {note.strip()}" if note.strip() else "") + "\nPode seguir com o que estava pendente.")


def reject(task: str, reason: str = "") -> dict:
    it = resolve_task(task)
    for p_ in pending_of(it["id"]):
        api("POST", f"/issues/{it['id']}/interactions/{p_['id']}/reject", {"reason": reason.strip() or "recusado pelo dono"})
    return change_state(task, "cancelled", "REJECTED" + (f": {reason.strip()}" if reason.strip() else ""))


def environment() -> dict:
    """Diagnóstico: quais variáveis do Paperclip chegaram a este servidor (só os nomes, nunca os valores)."""
    return {"ok": True, "api": API, "present": [k for k in ("PAPERCLIP_API_KEY", "PAPERCLIP_RUN_ID", "PAPERCLIP_TASK_ID", "PAPERCLIP_AGENT_ID") if os.environ.get(k)]}


TOOLS = {
    "environment": (environment, "Diagnóstico do Escritório: diz quais variáveis do Paperclip este servidor recebeu (só nomes).", {}, []),
    "create_task": (create_task, "Delega um trabalho que demora ao Escritório. Use quando o pedido não cabe numa resposta imediata (documento, pesquisa, proposta). Diga à pessoa que anotou e que avisa quando ficar pronto.",
                     {"title": "título curto da tarefa", "request": "o que precisa ser feito, com o contexto que a pessoa deu", "origin": "opcional: canal e conversa de origem (ex.: telegram:12345)",
                      "position": "opcional: nome do cargo que executa (veja a lista na descrição); vazio: o Assistente de trabalho"}, ["title", "request"]),
    "my_tasks": (my_tasks, "Lista as tarefas do Escritório (as 20 mais recentes), com estado: todo, in_progress, blocked (aguardando decisão), done (entregue), cancelled.",
                       {"state": "opcional: filtrar por estado"}, []),
    "approve": (approve, "Aprova uma pendência de uma tarefa bloqueada; o cargo retoma o trabalho. Pedir um ajuste é aprovar com nota: a nota vai para o cargo, que retoma com ela (não existe um comando 'ajustar').", {"task": "número (PES-12), id ou parte do título", "note": "opcional: o que mudar antes de entregar"}, ["task"]),
    "reject": (reject, "Recusa uma pendência e cancela a tarefa.", {"task": "número, id ou parte do título", "reason": "opcional"}, ["task"]),
    "comment": (comment, "Acrescenta um comentário a uma tarefa (progresso, dúvida).", {"task": "número ou id", "text": "o comentário"}, ["task", "text"]),
    "deliver": (deliver, "Cargo: conclui a tarefa com o resumo da entrega e o caminho do arquivo entregue.", {"task": "número ou id", "summary": "o que foi entregue, em duas ou três frases", "path": "caminho do arquivo da entrega"}, ["task", "summary"]),
    "request_approval": (request_approval, "Cargo: para a tarefa e pede uma decisão ao dono antes de uma ação fora do computador (enviar, pagar, publicar).", {"task": "número ou id", "question": "o que precisa ser decidido, em uma frase"}, ["task", "question"]),
}


def schema(name: str) -> dict:
    _, desc, props, req = TOOLS[name]
    if name == "create_task":
        cat = [c for c in positions_catalog() if c.get("id")]
        if len(cat) > 1:
            desc += " Cargos disponíveis: " + "; ".join(f"{c['name']} ({c['title']}: {c['does'] or 'tarefas em geral'})" for c in cat) + "."
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()}, "required": req}}


# ---------------------------------------------------------------- MCP (JSON-RPC por linhas no stdio)
def respond(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    # este processo fala com agentes, não com a interface: os erros das ferramentas seguem em português
    os.environ["JARVIS_LANGUAGE"] = "pt-BR"
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if method == "initialize":
            respond(id_, {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "office", "version": "0.1"}})
        elif method == "notifications/initialized" or id_ is None:
            continue
        elif method == "ping":
            respond(id_, {})
        elif method == "tools/list":
            respond(id_, {"tools": [schema(n) for n in TOOLS]})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in TOOLS:
                respond(id_, {"content": [{"type": "text", "text": f"ferramenta desconhecida: {name}"}], "isError": True})
                continue
            try:
                fn = TOOLS[name][0]
                out = fn(**{k: str(v) for k, v in args.items() if k in TOOLS[name][2]})
                respond(id_, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]})
            except (RuntimeError, TypeError) as e:
                respond(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            respond(id_, error={"code": -32601, "message": f"método desconhecido: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
