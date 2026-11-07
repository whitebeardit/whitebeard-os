#!/usr/bin/env python3
"""jarvis_escritorio_mcp.py — servidor MCP (stdio) do Escritório: as ferramentas com que os assistentes e os
cargos falam com o Paperclip sem terminal.

Para quem pede (o JARVIS do dono, assistentes da equipe): criar_tarefa, minhas_tarefas, aprovar, recusar.
Para quem executa (os cargos, dentro de um heartbeat do Paperclip): entregar, pedir_aprovacao, comentar.

Ambiente: PAPERCLIP_API_URL (padrão http://127.0.0.1:3100/api), PAPERCLIP_API_KEY (opcional: no modo local
não há chave), PAPERCLIP_RUN_ID (dentro de um heartbeat), ESCRITORIO_CARGO_ID (cargo padrão das tarefas novas),
ESCRITORIO_ORIGEM (para onde o carteiro avisa: 'telegram', 'telegram:<chat>', 'discord:#canal'…).
Só biblioteca padrão. Nunca escreve segredo na saída.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
if not API.endswith("/api"):
    API += "/api"
CHAVE = os.environ.get("PAPERCLIP_API_KEY", "")
RUN_ID = os.environ.get("PAPERCLIP_RUN_ID", "")
CARGO = os.environ.get("ESCRITORIO_CARGO_ID", "")
CARGO_NOME = os.environ.get("ESCRITORIO_CARGO_NOME", "Assistente de trabalho")
ORIGEM = os.environ.get("ESCRITORIO_ORIGEM", "telegram")
EMPRESA = os.environ.get("PAPERCLIP_COMPANY_ID", "")
JARVIS_HOME = os.environ.get("JARVIS_HOME", "")


def cargos_catalogo() -> list[dict]:
    """Os cargos em uso neste computador: nome, título, o que faz e o id do agente (de $JARVIS_HOME/escritorio/cargos.json)."""
    if not JARVIS_HOME:
        return []
    try:
        ids = json.loads(open(os.path.join(JARVIS_HOME, "escritorio", "cargos.json"), encoding="utf-8").read())
    except (OSError, ValueError):
        ids = {}
    try:
        render = json.loads(open(os.path.join(JARVIS_HOME, "render", "escritorio.json"), encoding="utf-8").read())
        cargos = [c for c in (render.get("cargos") or []) if isinstance(c, dict) and c.get("nome")]
    except (OSError, ValueError, AttributeError):
        cargos = []
    if not any(c.get("nome") == "trabalho" for c in cargos):
        cargos.insert(0, {"nome": "trabalho", "titulo": CARGO_NOME, "faz": "pesquisas, documentos, propostas, resumos"})
    out = []
    for c in cargos:
        reg = ids.get(c["nome"]) if isinstance(ids, dict) else None
        out.append({"nome": c["nome"], "titulo": c.get("titulo") or c["nome"], "faz": c.get("faz") or "", "id": (reg or {}).get("id", "")})
    return out


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if CHAVE:
        headers["Authorization"] = f"Bearer {CHAVE}"
    if RUN_ID and method in ("POST", "PATCH", "DELETE"):
        headers["X-Paperclip-Run-Id"] = RUN_ID
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Paperclip respondeu HTTP {e.code}: {detalhe}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("o Escritório (Paperclip) não está no ar neste computador") from e


def lista(d, chave):
    return d if isinstance(d, list) else (d or {}).get(chave, [])


def empresa_id() -> str:
    global EMPRESA
    if EMPRESA:
        return EMPRESA
    comp = lista(api("GET", "/companies"), "companies")
    if not comp:
        raise RuntimeError("nenhuma empresa no Escritório")
    EMPRESA = comp[0]["id"]
    return EMPRESA


def resolve_tarefa(ref: str) -> dict:
    """Aceita o id, o identificador (PES-12) ou parte do título."""
    ref = (ref or "").strip()
    if not ref:
        raise RuntimeError("diga qual tarefa")
    try:
        return api("GET", f"/issues/{ref}")
    except RuntimeError:
        pass
    for it in lista(api("GET", f"/companies/{empresa_id()}/issues"), "issues"):
        if it.get("identifier", "").lower() == ref.lower() or ref.lower() in (it.get("title") or "").lower():
            return it
    raise RuntimeError(f"tarefa não encontrada: {ref}")


def resumo_tarefa(it: dict) -> dict:
    return {"id": it.get("id"), "numero": it.get("identifier"), "titulo": it.get("title"), "estado": it.get("status"),
            "atualizada_em": it.get("updatedAt")}


# ---------------------------------------------------------------- ferramentas
def cargo_id() -> str:
    global CARGO
    if CARGO:
        return CARGO
    for ag in lista(api("GET", f"/companies/{empresa_id()}/agents"), "agents"):
        if ag.get("name") == CARGO_NOME and ag.get("status") != "terminated":
            CARGO = ag["id"]
            return CARGO
    raise RuntimeError("o Escritório não tem o cargo 'Assistente de trabalho' neste computador (coloque em uso)")


def cargo_escolhido(cargo: str) -> tuple[str, str]:
    """(id do agente, título) do cargo pedido, pelo nome ou pelo título; vazio ou desconhecido: o cargo padrão."""
    ref = (cargo or "").strip().lower()
    cat = cargos_catalogo()
    if ref:
        for c in cat:
            if ref in (c["nome"].lower(), c["titulo"].lower()) and c.get("id"):
                return c["id"], c["titulo"]
    padrao = next((c for c in cat if c["nome"] == "trabalho" and c.get("id")), None)
    if padrao:
        return padrao["id"], padrao["titulo"]
    return cargo_id(), CARGO_NOME


def criar_tarefa(titulo: str, pedido: str, origem: str = "", cargo: str = "") -> dict:
    if not titulo.strip():
        raise RuntimeError("a tarefa precisa de um título")
    agente, titulo_cargo = cargo_escolhido(cargo)
    desc = (pedido or "").strip() + f"\n\n[origem: {origem.strip() or ORIGEM}]"
    it = api("POST", f"/companies/{empresa_id()}/issues",
             {"title": titulo.strip()[:200], "description": desc, "status": "todo", "priority": "medium", "assigneeAgentId": agente})
    return {"ok": True, "tarefa": resumo_tarefa(it), "cargo": titulo_cargo, "mensagem": f"Tarefa {it.get('identifier')} anotada com o cargo {titulo_cargo}. Aviso quando ficar pronta."}


def minhas_tarefas(estado: str = "") -> dict:
    itens = lista(api("GET", f"/companies/{empresa_id()}/issues"), "issues")
    if estado:
        itens = [i for i in itens if i.get("status") == estado]
    itens.sort(key=lambda i: i.get("updatedAt") or "", reverse=True)
    return {"ok": True, "tarefas": [resumo_tarefa(i) for i in itens[:20]]}


def comentar(tarefa: str, texto: str) -> dict:
    it = resolve_tarefa(tarefa)
    api("POST", f"/issues/{it['id']}/comments", {"body": texto})
    return {"ok": True, "tarefa": resumo_tarefa(it)}


def mudar(tarefa: str, estado: str, comentario: str) -> dict:
    it = resolve_tarefa(tarefa)
    if RUN_ID:
        # dentro de um heartbeat: uma escrita só, atribuída ao agente (status + comentário), que não o acorda de novo
        novo = api("PATCH", f"/issues/{it['id']}", {"status": estado, "comment": comentario})
    else:
        api("POST", f"/issues/{it['id']}/comments", {"body": comentario})
        novo = api("PATCH", f"/issues/{it['id']}", {"status": estado})
    return {"ok": True, "tarefa": resumo_tarefa(novo or it)}


def entregar(tarefa: str, resumo: str, caminho: str = "") -> dict:
    texto = "ENTREGA: " + resumo.strip() + (f"\nArquivo: {caminho.strip()}" if caminho.strip() else "")
    return mudar(tarefa, "done", texto)


def pendencias_de(issue_id: str) -> list[dict]:
    """Interações de confirmação ainda abertas na tarefa (as pendências do dono)."""
    its = lista(api("GET", f"/issues/{issue_id}/interactions"), "interactions")
    return [i for i in its if i.get("kind") == "request_confirmation" and i.get("status") in ("pending", "open", "awaiting")]


def pedir_aprovacao(tarefa: str, pergunta: str) -> dict:
    """Cargo: cria a pendência (interação de confirmação do Paperclip) e a tarefa fica bloqueada até a decisão.
    Com continuationPolicy wake_assignee, o cargo acorda sozinho depois que o dono responde."""
    it = resolve_tarefa(tarefa)
    api("POST", f"/issues/{it['id']}/interactions", {
        "kind": "request_confirmation", "title": "Precisa da sua decisão", "continuationPolicy": "wake_assignee",
        "payload": {"version": 1, "prompt": pergunta.strip()[:1000], "acceptLabel": "Aprovar", "rejectLabel": "Recusar"}})
    try:
        api("PATCH", f"/issues/{it['id']}", {"status": "blocked", "comment": "PENDÊNCIA: " + pergunta.strip()})
    except RuntimeError:
        api("POST", f"/issues/{it['id']}/comments", {"body": "PENDÊNCIA: " + pergunta.strip()})
    return {"ok": True, "tarefa": resumo_tarefa(it), "mensagem": "Pendência registrada; o dono foi avisado. Pare por aqui: você será acordado com a resposta."}


def aprovar(tarefa: str, nota: str = "") -> dict:
    it = resolve_tarefa(tarefa)
    pend = pendencias_de(it["id"])
    if pend:
        # a nota vai para a descrição (não acorda o cargo); o aceite da pendência é o único despertar, senão
        # comentário + aceite viram duas execuções paralelas e duas entregas
        if nota.strip():
            api("PATCH", f"/issues/{it['id']}", {"description": (it.get("description") or "") + f"\n\nNota do dono ao aprovar: {nota.strip()}"})
        for p_ in pend:
            api("POST", f"/issues/{it['id']}/interactions/{p_['id']}/accept", {})
        return {"ok": True, "tarefa": resumo_tarefa(it), "mensagem": "Aprovado; o cargo retoma o trabalho."}
    return mudar(tarefa, "todo", "APROVADO" + (f": {nota.strip()}" if nota.strip() else "") + "\nPode seguir com o que estava pendente.")


def recusar(tarefa: str, motivo: str = "") -> dict:
    it = resolve_tarefa(tarefa)
    for p_ in pendencias_de(it["id"]):
        api("POST", f"/issues/{it['id']}/interactions/{p_['id']}/reject", {"reason": motivo.strip() or "recusado pelo dono"})
    return mudar(tarefa, "cancelled", "RECUSADO" + (f": {motivo.strip()}" if motivo.strip() else ""))


def ambiente() -> dict:
    """Diagnóstico: quais variáveis do Paperclip chegaram a este servidor (só os nomes, nunca os valores)."""
    return {"ok": True, "api": API, "presentes": [k for k in ("PAPERCLIP_API_KEY", "PAPERCLIP_RUN_ID", "PAPERCLIP_TASK_ID", "PAPERCLIP_AGENT_ID") if os.environ.get(k)]}


FERRAMENTAS = {
    "ambiente": (ambiente, "Diagnóstico do Escritório: diz quais variáveis do Paperclip este servidor recebeu (só nomes).", {}, []),
    "criar_tarefa": (criar_tarefa, "Delega um trabalho que demora ao Escritório. Use quando o pedido não cabe numa resposta imediata (documento, pesquisa, proposta). Diga à pessoa que anotou e que avisa quando ficar pronto.",
                     {"titulo": "título curto da tarefa", "pedido": "o que precisa ser feito, com o contexto que a pessoa deu", "origem": "opcional: canal e conversa de origem (ex.: telegram:12345)",
                      "cargo": "opcional: nome do cargo que executa (veja a lista na descrição); vazio: o Assistente de trabalho"}, ["titulo", "pedido"]),
    "minhas_tarefas": (minhas_tarefas, "Lista as tarefas do Escritório (as 20 mais recentes), com estado: todo, in_progress, blocked (aguardando decisão), done (entregue), cancelled.",
                       {"estado": "opcional: filtrar por estado"}, []),
    "aprovar": (aprovar, "Aprova uma pendência de uma tarefa bloqueada; o cargo retoma o trabalho. Pedir um ajuste é aprovar com nota: a nota vai para o cargo, que retoma com ela (não existe um comando 'ajustar').", {"tarefa": "número (PES-12), id ou parte do título", "nota": "opcional: o que mudar antes de entregar"}, ["tarefa"]),
    "recusar": (recusar, "Recusa uma pendência e cancela a tarefa.", {"tarefa": "número, id ou parte do título", "motivo": "opcional"}, ["tarefa"]),
    "comentar": (comentar, "Acrescenta um comentário a uma tarefa (progresso, dúvida).", {"tarefa": "número ou id", "texto": "o comentário"}, ["tarefa", "texto"]),
    "entregar": (entregar, "Cargo: conclui a tarefa com o resumo da entrega e o caminho do arquivo entregue.", {"tarefa": "número ou id", "resumo": "o que foi entregue, em duas ou três frases", "caminho": "caminho do arquivo da entrega"}, ["tarefa", "resumo"]),
    "pedir_aprovacao": (pedir_aprovacao, "Cargo: para a tarefa e pede uma decisão ao dono antes de uma ação fora do computador (enviar, pagar, publicar).", {"tarefa": "número ou id", "pergunta": "o que precisa ser decidido, em uma frase"}, ["tarefa", "pergunta"]),
}


def esquema(nome: str) -> dict:
    _, desc, props, req = FERRAMENTAS[nome]
    if nome == "criar_tarefa":
        cat = [c for c in cargos_catalogo() if c.get("id")]
        if len(cat) > 1:
            desc += " Cargos disponíveis: " + "; ".join(f"{c['nome']} ({c['titulo']}: {c['faz'] or 'tarefas em geral'})" for c in cat) + "."
    return {"name": nome, "description": desc,
            "inputSchema": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()}, "required": req}}


# ---------------------------------------------------------------- MCP (JSON-RPC por linhas no stdio)
def responder(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for linha in sys.stdin:
        linha = linha.strip()
        if not linha:
            continue
        try:
            req = json.loads(linha)
        except ValueError:
            continue
        metodo, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if metodo == "initialize":
            responder(id_, {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "escritorio", "version": "0.1"}})
        elif metodo == "notifications/initialized" or id_ is None:
            continue
        elif metodo == "ping":
            responder(id_, {})
        elif metodo == "tools/list":
            responder(id_, {"tools": [esquema(n) for n in FERRAMENTAS]})
        elif metodo == "tools/call":
            nome = params.get("name")
            args = params.get("arguments") or {}
            if nome not in FERRAMENTAS:
                responder(id_, {"content": [{"type": "text", "text": f"ferramenta desconhecida: {nome}"}], "isError": True})
                continue
            try:
                fn = FERRAMENTAS[nome][0]
                out = fn(**{k: str(v) for k, v in args.items() if k in FERRAMENTAS[nome][2]})
                responder(id_, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]})
            except (RuntimeError, TypeError) as e:
                responder(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            responder(id_, error={"code": -32601, "message": f"método desconhecido: {metodo}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
