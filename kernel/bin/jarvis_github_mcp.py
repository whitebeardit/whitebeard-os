#!/usr/bin/env python3
"""jarvis_github_mcp.py — servidor MCP (stdio) de leitura de repositórios públicos do GitHub: as ferramentas com
que os assistentes leem código (exercícios, projetos) direto do GitHub, sem serviço de extração no meio.

Ferramentas: list_repository (a árvore de arquivos), read_file (o conteúdo cru de um arquivo).
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
FILE_LIMIT = 200 * 1024  # bytes devolvidos por arquivo
LIST_LIMIT = 400  # arquivos por listagem
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class Refusal(Exception):
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
            return r.read(FILE_LIMIT * 4 + 1), dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Refusal("não encontrado: o repositório é privado, não existe, ou o caminho/branch está errado") from None
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            raise Refusal("o limite de consultas à API do GitHub acabou por esta hora"
                         + ("" if TOKEN else " (sem GITHUB_TOKEN são 60 por hora; read_file continua funcionando)")) from None
        raise Refusal(f"o GitHub respondeu HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Refusal(f"não consegui falar com o GitHub: {getattr(e, 'reason', e)}") from None


def parse_repo(repository: str) -> tuple[str, str, str, str]:
    """'dono/repo', 'https://github.com/dono/repo[/tree|blob/ref/caminho]' → (dono, repo, ref, caminho)."""
    r = (repository or "").strip()
    if not r:
        raise Refusal("diga o repositório: dono/nome ou o link do GitHub")
    if "://" in r or r.startswith("github.com"):
        u = urllib.parse.urlparse(r if "://" in r else "https://" + r)
        if u.hostname not in ("github.com", "www.github.com"):
            raise Refusal("só repositórios de github.com")
        parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
    else:
        parts = [p for p in r.split("/") if p]
    if len(parts) < 2:
        raise Refusal("repositório inválido: use dono/nome")
    owner, name = parts[0], parts[1].removesuffix(".git")
    if not (NAME_RE.match(owner) and NAME_RE.match(name)):
        raise Refusal("repositório inválido: use dono/nome")
    ref, path = "", ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref, path = parts[3], "/".join(parts[4:])
    return owner, name, ref, path


def clean_path(c: str) -> str:
    parts = [p for p in (c or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Refusal("caminho com '..' não é aceito")
    return "/".join(parts)


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decodifica respeitando BOM (há alunos que salvam em UTF-16). Devolve (texto, codificação)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8 (com BOM)"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf-16"
    if b"\x00" in raw[:8000]:
        raise Refusal("o arquivo parece binário (imagem, executável, compactado); não dá para ler como texto")
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


# ---------------------------------------------------------------- ferramentas
def list_repository(repository: str, path: str = "", ref: str = "") -> dict:
    owner, name, ref_url, url_path = parse_repo(repository)
    ref = (ref or ref_url or "HEAD").strip()
    folder = clean_path(path or url_path)
    body, resp_headers = http(f"https://api.github.com/repos/{owner}/{name}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1", api=True)
    try:
        tree = json.loads(body)
    except ValueError:
        raise Refusal("resposta inesperada do GitHub") from None
    items = [t for t in tree.get("tree") or [] if t.get("type") == "blob"]
    if folder:
        items = [t for t in items if t["path"] == folder or t["path"].startswith(folder + "/")]
    files = [{"path": t["path"], "bytes": t.get("size", 0)} for t in items[:LIST_LIMIT]]
    out = {"repository": f"{owner}/{name}", "ref": ref, "total": len(items), "files": files,
           "link": f"https://github.com/{owner}/{name}" + (f"/tree/{ref}/{folder}" if folder and ref != "HEAD" else "")}
    if len(items) > LIST_LIMIT or tree.get("truncated"):
        out["warning"] = "lista cortada: passe `path` para listar uma pasta"
    if resp_headers.get("X-RateLimit-Remaining") is not None:
        out["api_requests_remaining"] = int(resp_headers["X-RateLimit-Remaining"])
    return out


def read_file(repository: str, path: str = "", ref: str = "") -> dict:
    owner, name, ref_url, url_path = parse_repo(repository)
    ref = (ref or ref_url or "HEAD").strip()
    file_path = clean_path(path or url_path)
    if not file_path:
        raise Refusal("diga o caminho do arquivo (use list_repository para ver os arquivos)")
    url = f"https://raw.githubusercontent.com/{owner}/{name}/{urllib.parse.quote(ref, safe='')}/{urllib.parse.quote(file_path)}"
    raw, _ = http(url, api=False)
    text, encoding_name = decode_text(raw[:FILE_LIMIT])
    out = {"repository": f"{owner}/{name}", "ref": ref, "path": file_path, "encoding": encoding_name, "content": text,
           "link": f"https://github.com/{owner}/{name}/blob/{ref}/{file_path}"}
    if len(raw) > FILE_LIMIT:
        out["warning"] = f"arquivo cortado nos primeiros {FILE_LIMIT // 1024} KB"
    return out


TOOLS = {
    "list_repository": (list_repository,
                           "Lista os arquivos de um repositório público do GitHub (caminho e tamanho). Use primeiro, para saber o que ler. Aceita dono/nome ou o link do repositório (também links de pasta, /tree/...).",
                           {"repository": "dono/nome ou o link https://github.com/...",
                            "path": "opcional: só esta pasta",
                            "ref": "opcional: branch, tag ou commit (padrão: o branch principal)"}, ["repository"]),
    "read_file": (read_file,
                    "Lê o conteúdo de um arquivo de um repositório público do GitHub, como texto cru (README, .js, .py, .txt...). Detecta UTF-16 e outras codificações. Aceita também o link do arquivo (/blob/...).",
                    {"repository": "dono/nome, o link do repositório ou o link do arquivo",
                     "path": "caminho do arquivo no repositório (dispensável se o link já for do arquivo)",
                     "ref": "opcional: branch, tag ou commit (padrão: o branch principal)"}, ["repository"]),
}


def schema(name: str) -> dict:
    _, desc, props, req = TOOLS[name]
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
                            "serverInfo": {"name": "github", "version": "0.1"}})
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
            except (Refusal, TypeError) as e:
                respond(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            respond(id_, error={"code": -32601, "message": f"método desconhecido: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
