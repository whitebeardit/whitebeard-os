#!/usr/bin/env python3
"""jarvis_cerebro_mcp.py — servidor MCP (stdio) de escrita no second brain: as ferramentas com que os
assistentes (o JARVIS do dono e os da equipe) leem e gravam notas Markdown no vault, sem acesso a arquivos.

O MCP `knowledge` (GBrain) continua sendo a busca; este grava os arquivos, que são a fonte da verdade. O kernel
faz o commit e sincroniza o cérebro sozinho, então a nota aparece na busca no ciclo seguinte.

Ferramentas: ler_nota, escrever_nota, editar_nota.
Travas: lê só arquivos .md dentro do vault; grava só dentro de tenants/<tenant>/; recusa `..`, caminho absoluto,
link simbólico e nota grande; não apaga nada; não faz commit. Gravar uma nota que já existe exige a `versao`
lida antes (evita sobrescrever o que outro editou nesse meio-tempo).

Ambiente: VAULT_PATH (pasta do vault), JARVIS_TENANT (tenant). Só biblioteca padrão.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

VAULT = os.environ.get("VAULT_PATH", "")
TENANT = os.environ.get("JARVIS_TENANT", "")
LIMITE = 512 * 1024  # bytes por nota


class Recusa(Exception):
    pass


def raiz() -> Path:
    if not VAULT or not Path(VAULT).is_dir():
        raise Recusa("o vault não está configurado neste computador (VAULT_PATH)")
    return Path(VAULT).resolve()


def resolver(caminho: str, gravar: bool) -> tuple[Path, str]:
    """Caminho relativo ao vault → arquivo absoluto, conferindo as travas. Devolve (arquivo, caminho normalizado)."""
    c = (caminho or "").strip().replace("\\", "/")
    if not c:
        raise Recusa("diga o caminho da nota, relativo ao vault (ex.: tenants/<tenant>/pasta/nota.md)")
    if c.startswith("/") or c.startswith("~"):
        raise Recusa("use um caminho relativo ao vault, não absoluto")
    partes = [p for p in c.split("/") if p not in ("", ".")]
    if any(p == ".." for p in partes):
        raise Recusa("caminho com '..' não é aceito")
    if any(p.startswith(".") for p in partes):
        raise Recusa("pastas e arquivos ocultos não são aceitos")
    if not partes[-1].lower().endswith(".md"):
        partes[-1] += ".md"
    rel = "/".join(partes)
    base = raiz()
    if gravar:
        if not TENANT:
            raise Recusa("o tenant não está configurado (JARVIS_TENANT)")
        if partes[:2] != ["tenants", TENANT] or len(partes) < 3:
            raise Recusa(f"só é possível gravar dentro de tenants/{TENANT}/")
    alvo = base.joinpath(*partes)
    # nenhum pedaço do caminho pode ser link simbólico (senão o arquivo sairia do vault)
    p = base
    for parte in partes:
        p = p / parte
        if p.is_symlink():
            raise Recusa("caminho com link simbólico não é aceito")
    try:
        alvo.resolve().relative_to(base)
    except ValueError:
        raise Recusa("o caminho sai do vault") from None
    return alvo, rel


def versao_de(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def ler(alvo: Path) -> str:
    if alvo.stat().st_size > LIMITE:
        raise Recusa("a nota é grande demais para este MCP")
    return alvo.read_text(encoding="utf-8")


def gravar(alvo: Path, texto: str) -> None:
    dados = texto.encode("utf-8")
    if len(dados) > LIMITE:
        raise Recusa(f"o conteúdo passa de {LIMITE // 1024} KB; divida em mais de uma nota")
    alvo.parent.mkdir(parents=True, exist_ok=True)
    # escrita atômica: grava ao lado e troca, para a sincronização nunca ver meio arquivo
    fd, tmp = tempfile.mkstemp(dir=alvo.parent, prefix=".cerebro-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dados if dados.endswith(b"\n") else dados + b"\n")
        if alvo.exists():
            os.chmod(tmp, alvo.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, alvo)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- ferramentas
def ler_nota(caminho: str) -> dict:
    alvo, rel = resolver(caminho, gravar=False)
    if not alvo.is_file():
        return {"caminho": rel, "existe": False, "versao": "", "conteudo": ""}
    texto = ler(alvo)
    return {"caminho": rel, "existe": True, "versao": versao_de(texto), "conteudo": texto}


def escrever_nota(caminho: str, conteudo: str, versao: str = "") -> dict:
    alvo, rel = resolver(caminho, gravar=True)
    if alvo.exists():
        if not alvo.is_file():
            raise Recusa("já existe uma pasta com esse nome")
        atual = versao_de(ler(alvo))
        if not versao:
            raise Recusa("a nota já existe: leia com ler_nota e mande a versao para substituir, ou use editar_nota")
        if versao != atual:
            raise Recusa("a nota mudou desde que você leu (versão diferente): leia de novo e refaça a alteração")
        criada = False
    else:
        criada = True
    gravar(alvo, conteudo)
    return {"caminho": rel, "criada": criada, "versao": versao_de(ler(alvo))}


def editar_nota(caminho: str, trocar: str, por: str, versao: str = "") -> dict:
    alvo, rel = resolver(caminho, gravar=True)
    if not alvo.is_file():
        raise Recusa("a nota não existe: crie com escrever_nota")
    texto = ler(alvo)
    if versao and versao != versao_de(texto):
        raise Recusa("a nota mudou desde que você leu (versão diferente): leia de novo e refaça a alteração")
    if not trocar:
        raise Recusa("diga o trecho exato a trocar (trocar)")
    n = texto.count(trocar)
    if n == 0:
        raise Recusa("o trecho a trocar não está na nota: leia de novo e copie o trecho exato")
    if n > 1:
        raise Recusa(f"o trecho aparece {n} vezes; inclua mais texto em volta para ficar único")
    gravar(alvo, texto.replace(trocar, por, 1))
    return {"caminho": rel, "versao": versao_de(ler(alvo))}


FERRAMENTAS = {
    "ler_nota": (ler_nota,
                 "Lê uma nota Markdown do second brain (vault) e devolve o conteúdo e a versão. Se a nota não existe, devolve existe=false. Use antes de alterar uma nota.",
                 {"caminho": "caminho relativo ao vault, ex.: tenants/<tenant>/pasta/nota.md"}, ["caminho"]),
    "escrever_nota": (escrever_nota,
                      "Cria uma nota Markdown no second brain, ou a substitui por inteiro. Para substituir, mande a versao que ler_nota devolveu. Grava só dentro de tenants/<tenant>/. Não apaga nada; o kernel faz o commit e a nota entra na busca no ciclo seguinte.",
                      {"caminho": "caminho relativo ao vault, dentro de tenants/<tenant>/, terminando em .md",
                       "conteudo": "o Markdown completo da nota, com frontmatter",
                       "versao": "obrigatória ao substituir uma nota que já existe: a versão devolvida por ler_nota"}, ["caminho", "conteudo"]),
    "editar_nota": (editar_nota,
                    "Troca um trecho exato de uma nota do second brain (o trecho deve aparecer uma vez só). Bom para acrescentar uma linha numa tabela ou atualizar um parágrafo sem reescrever a nota.",
                    {"caminho": "caminho relativo ao vault, dentro de tenants/<tenant>/",
                     "trocar": "o trecho atual, copiado exatamente da nota",
                     "por": "o texto novo que fica no lugar",
                     "versao": "opcional: a versão lida, para recusar se a nota mudou"}, ["caminho", "trocar", "por"]),
}


def esquema(nome: str) -> dict:
    _, desc, props, req = FERRAMENTAS[nome]
    if TENANT:
        desc = desc.replace("<tenant>", TENANT)
        props = {k: v.replace("<tenant>", TENANT) for k, v in props.items()}
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
                            "serverInfo": {"name": "cerebro", "version": "0.1"}})
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
            except (Recusa, TypeError, OSError, UnicodeDecodeError) as e:
                responder(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            responder(id_, error={"code": -32601, "message": f"método desconhecido: {metodo}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
