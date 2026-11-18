#!/usr/bin/env python3
"""jarvis_github_mcp.py — servidor MCP (stdio) de leitura de repositórios públicos do GitHub: as ferramentas com
que os assistentes leem código (exercícios, projetos) direto do GitHub, sem serviço de extração no meio.

Ferramentas: listar_repositorio (a árvore de arquivos), ler_arquivo (o conteúdo cru de um arquivo).
Só leitura e só github.com: a listagem usa a API (api.github.com, 60 consultas por hora sem token, 5.000 com
GITHUB_TOKEN); a leitura de arquivo usa raw.githubusercontent.com, que não gasta esse limite.

Ambiente: GITHUB_TOKEN (opcional; um token sem permissão nenhuma basta para repositórios públicos).
Só biblioteca padrão. Nunca escreve o token na saída.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if TOKEN.startswith("${"):  # variável não resolvida pelo Hermes: sem token
    TOKEN = ""
LIMITE_ARQUIVO = 200 * 1024  # bytes devolvidos por arquivo
LIMITE_LISTA = 400  # arquivos por listagem
NOME = re.compile(r"^[A-Za-z0-9_.-]+$")


class Recusa(Exception):
    pass


def http(url: str, api: bool) -> tuple[bytes, dict]:
    headers = {"User-Agent": "whitebeard-os-github-mcp"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(LIMITE_ARQUIVO * 4 + 1), dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Recusa("não encontrado: o repositório é privado, não existe, ou o caminho/branch está errado") from None
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            raise Recusa("o limite de consultas à API do GitHub acabou por esta hora"
                         + ("" if TOKEN else " (sem GITHUB_TOKEN são 60 por hora; ler_arquivo continua funcionando)")) from None
        raise Recusa(f"o GitHub respondeu HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Recusa(f"não consegui falar com o GitHub: {getattr(e, 'reason', e)}") from None


def repo_de(repositorio: str) -> tuple[str, str, str, str]:
    """'dono/repo', 'https://github.com/dono/repo[/tree|blob/ref/caminho]' → (dono, repo, ref, caminho)."""
    r = (repositorio or "").strip()
    if not r:
        raise Recusa("diga o repositório: dono/nome ou o link do GitHub")
    if "://" in r or r.startswith("github.com"):
        u = urllib.parse.urlparse(r if "://" in r else "https://" + r)
        if u.hostname not in ("github.com", "www.github.com"):
            raise Recusa("só repositórios de github.com")
        partes = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
    else:
        partes = [p for p in r.split("/") if p]
    if len(partes) < 2:
        raise Recusa("repositório inválido: use dono/nome")
    dono, nome = partes[0], partes[1].removesuffix(".git")
    if not (NOME.match(dono) and NOME.match(nome)):
        raise Recusa("repositório inválido: use dono/nome")
    ref, caminho = "", ""
    if len(partes) >= 4 and partes[2] in ("tree", "blob"):
        ref, caminho = partes[3], "/".join(partes[4:])
    return dono, nome, ref, caminho


def limpar_caminho(c: str) -> str:
    partes = [p for p in (c or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in partes):
        raise Recusa("caminho com '..' não é aceito")
    return "/".join(partes)


def texto_de(dados: bytes) -> tuple[str, str]:
    """Decodifica respeitando BOM (há alunos que salvam em UTF-16). Devolve (texto, codificação)."""
    if dados.startswith(b"\xef\xbb\xbf"):
        return dados[3:].decode("utf-8", "replace"), "utf-8 (com BOM)"
    if dados.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dados.decode("utf-16", "replace"), "utf-16"
    if b"\x00" in dados[:8000]:
        raise Recusa("o arquivo parece binário (imagem, executável, compactado); não dá para ler como texto")
    try:
        return dados.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return dados.decode("latin-1"), "latin-1"


# ---------------------------------------------------------------- ferramentas
def listar_repositorio(repositorio: str, caminho: str = "", ref: str = "") -> dict:
    dono, nome, ref_url, cam_url = repo_de(repositorio)
    ref = (ref or ref_url or "HEAD").strip()
    pasta = limpar_caminho(caminho or cam_url)
    corpo, cab = http(f"https://api.github.com/repos/{dono}/{nome}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1", api=True)
    try:
        arvore = json.loads(corpo)
    except ValueError:
        raise Recusa("resposta inesperada do GitHub") from None
    itens = [t for t in arvore.get("tree") or [] if t.get("type") == "blob"]
    if pasta:
        itens = [t for t in itens if t["path"] == pasta or t["path"].startswith(pasta + "/")]
    arquivos = [{"caminho": t["path"], "bytes": t.get("size", 0)} for t in itens[:LIMITE_LISTA]]
    out = {"repositorio": f"{dono}/{nome}", "ref": ref, "total": len(itens), "arquivos": arquivos,
           "link": f"https://github.com/{dono}/{nome}" + (f"/tree/{ref}/{pasta}" if pasta and ref != "HEAD" else "")}
    if len(itens) > LIMITE_LISTA or arvore.get("truncated"):
        out["aviso"] = "lista cortada: passe `caminho` para listar uma pasta"
    if cab.get("X-RateLimit-Remaining") is not None:
        out["consultas_restantes_api"] = int(cab["X-RateLimit-Remaining"])
    return out


def ler_arquivo(repositorio: str, caminho: str = "", ref: str = "") -> dict:
    dono, nome, ref_url, cam_url = repo_de(repositorio)
    ref = (ref or ref_url or "HEAD").strip()
    cam = limpar_caminho(caminho or cam_url)
    if not cam:
        raise Recusa("diga o caminho do arquivo (use listar_repositorio para ver os arquivos)")
    url = f"https://raw.githubusercontent.com/{dono}/{nome}/{urllib.parse.quote(ref, safe='')}/{urllib.parse.quote(cam)}"
    dados, _ = http(url, api=False)
    texto, cod = texto_de(dados[:LIMITE_ARQUIVO])
    out = {"repositorio": f"{dono}/{nome}", "ref": ref, "caminho": cam, "codificacao": cod, "conteudo": texto,
           "link": f"https://github.com/{dono}/{nome}/blob/{ref}/{cam}"}
    if len(dados) > LIMITE_ARQUIVO:
        out["aviso"] = f"arquivo cortado nos primeiros {LIMITE_ARQUIVO // 1024} KB"
    return out


FERRAMENTAS = {
    "listar_repositorio": (listar_repositorio,
                           "Lista os arquivos de um repositório público do GitHub (caminho e tamanho). Use primeiro, para saber o que ler. Aceita dono/nome ou o link do repositório (também links de pasta, /tree/...).",
                           {"repositorio": "dono/nome ou o link https://github.com/...",
                            "caminho": "opcional: só esta pasta",
                            "ref": "opcional: branch, tag ou commit (padrão: o branch principal)"}, ["repositorio"]),
    "ler_arquivo": (ler_arquivo,
                    "Lê o conteúdo de um arquivo de um repositório público do GitHub, como texto cru (README, .js, .py, .txt...). Detecta UTF-16 e outras codificações. Aceita também o link do arquivo (/blob/...).",
                    {"repositorio": "dono/nome, o link do repositório ou o link do arquivo",
                     "caminho": "caminho do arquivo no repositório (dispensável se o link já for do arquivo)",
                     "ref": "opcional: branch, tag ou commit (padrão: o branch principal)"}, ["repositorio"]),
}


def esquema(nome: str) -> dict:
    _, desc, props, req = FERRAMENTAS[nome]
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
                            "serverInfo": {"name": "github", "version": "0.1"}})
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
            except (Recusa, TypeError) as e:
                responder(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            responder(id_, error={"code": -32601, "message": f"método desconhecido: {metodo}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
