#!/usr/bin/env python3
"""jarvis_vault_mcp.py — servidor MCP (stdio) de escrita no second brain: as ferramentas com que os
assistentes (o JARVIS do dono e os da equipe) leem e gravam notas Markdown no vault, sem acesso a arquivos.

O MCP `knowledge` (GBrain) continua sendo a busca; este grava os arquivos, que são a fonte da verdade. O kernel
faz o commit e sincroniza o cérebro sozinho, então a nota aparece na busca no ciclo seguinte.

Ferramentas: read_note, write_note, edit_note.
Travas: lê só arquivos .md dentro do vault; grava só dentro de tenants/<tenant>/; recusa `..`, caminho absoluto,
link simbólico e nota grande; não apaga nada; não faz commit. Gravar uma nota que já existe exige a `version`
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
LIMIT = 512 * 1024  # bytes por nota


class Refusal(Exception):
    pass


def vault_root() -> Path:
    if not VAULT or not Path(VAULT).is_dir():
        raise Refusal("o vault não está configurado neste computador (VAULT_PATH)")
    return Path(VAULT).resolve()


def resolve_note(path: str, save: bool) -> tuple[Path, str]:
    """Caminho relativo ao vault → arquivo absoluto, conferindo as travas. Devolve (arquivo, caminho normalizado)."""
    c = (path or "").strip().replace("\\", "/")
    if not c:
        raise Refusal("diga o caminho da nota, relativo ao vault (ex.: tenants/<tenant>/pasta/nota.md)")
    if c.startswith("/") or c.startswith("~"):
        raise Refusal("use um caminho relativo ao vault, não absoluto")
    parts = [p for p in c.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Refusal("caminho com '..' não é aceito")
    if any(p.startswith(".") for p in parts):
        raise Refusal("pastas e arquivos ocultos não são aceitos")
    if not parts[-1].lower().endswith(".md"):
        parts[-1] += ".md"
    rel = "/".join(parts)
    base = vault_root()
    if save:
        if not TENANT:
            raise Refusal("o tenant não está configurado (JARVIS_TENANT)")
        if parts[:2] != ["tenants", TENANT] or len(parts) < 3:
            raise Refusal(f"só é possível gravar dentro de tenants/{TENANT}/")
    target = base.joinpath(*parts)
    # nenhum pedaço do caminho pode ser link simbólico (senão o arquivo sairia do vault)
    p = base
    for part in parts:
        p = p / part
        if p.is_symlink():
            raise Refusal("caminho com link simbólico não é aceito")
    try:
        target.resolve().relative_to(base)
    except ValueError:
        raise Refusal("o caminho sai do vault") from None
    return target, rel


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load(target: Path) -> str:
    if target.stat().st_size > LIMIT:
        raise Refusal("a nota é grande demais para este MCP")
    return target.read_text(encoding="utf-8")


def save(target: Path, text: str) -> None:
    raw = text.encode("utf-8")
    if len(raw) > LIMIT:
        raise Refusal(f"o conteúdo passa de {LIMIT // 1024} KB; divida em mais de uma nota")
    target.parent.mkdir(parents=True, exist_ok=True)
    # escrita atômica: grava ao lado e troca, para a sincronização nunca ver meio arquivo
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".vault-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw if raw.endswith(b"\n") else raw + b"\n")
        if target.exists():
            os.chmod(tmp, target.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- ferramentas
def read_note(path: str) -> dict:
    target, rel = resolve_note(path, save=False)
    if not target.is_file():
        return {"path": rel, "exists": False, "version": "", "content": ""}
    text = load(target)
    return {"path": rel, "exists": True, "version": version_of(text), "content": text}


def write_note(path: str, content: str, version: str = "") -> dict:
    target, rel = resolve_note(path, save=True)
    if target.exists():
        if not target.is_file():
            raise Refusal("já existe uma pasta com esse nome")
        current = version_of(load(target))
        if not version:
            raise Refusal("a nota já existe: leia com read_note e mande a `version` para substituir, ou use edit_note")
        if version != current:
            raise Refusal("a nota mudou desde que você leu (versão diferente): leia de novo e refaça a alteração")
        created = False
    else:
        created = True
    save(target, content)
    return {"path": rel, "created": created, "version": version_of(load(target))}


def edit_note(path: str, old_text: str, new_text: str, version: str = "") -> dict:
    target, rel = resolve_note(path, save=True)
    if not target.is_file():
        raise Refusal("a nota não existe: crie com write_note")
    text = load(target)
    if version and version != version_of(text):
        raise Refusal("a nota mudou desde que você leu (versão diferente): leia de novo e refaça a alteração")
    if not old_text:
        raise Refusal("diga o trecho exato a trocar (old_text)")
    n = text.count(old_text)
    if n == 0:
        raise Refusal("o trecho a trocar não está na nota: leia de novo e copie o trecho exato")
    if n > 1:
        raise Refusal(f"o trecho aparece {n} vezes; inclua mais texto em volta para ficar único")
    save(target, text.replace(old_text, new_text, 1))
    return {"path": rel, "version": version_of(load(target))}


TOOLS = {
    "read_note": (read_note,
                 "Lê uma nota Markdown do second brain (vault) e devolve o conteúdo e a versão. Se a nota não existe, devolve exists=false. Use antes de alterar uma nota.",
                 {"path": "caminho relativo ao vault, ex.: tenants/<tenant>/pasta/nota.md"}, ["path"]),
    "write_note": (write_note,
                      "Cria uma nota Markdown no second brain, ou a substitui por inteiro. Para substituir, mande a `version` que read_note devolveu. Grava só dentro de tenants/<tenant>/. Não apaga nada; o kernel faz o commit e a nota entra na busca no ciclo seguinte.",
                      {"path": "caminho relativo ao vault, dentro de tenants/<tenant>/, terminando em .md",
                       "content": "o Markdown completo da nota, com frontmatter",
                       "version": "obrigatória ao substituir uma nota que já existe: a versão devolvida por read_note"}, ["path", "content"]),
    "edit_note": (edit_note,
                    "Troca um trecho exato de uma nota do second brain (o trecho deve aparecer uma vez só). Bom para acrescentar uma linha numa tabela ou atualizar um parágrafo sem reescrever a nota.",
                    {"path": "caminho relativo ao vault, dentro de tenants/<tenant>/",
                     "old_text": "o trecho atual, copiado exatamente da nota",
                     "new_text": "o texto novo que fica no lugar",
                     "version": "opcional: a versão lida, para recusar se a nota mudou"}, ["path", "old_text", "new_text"]),
}


def schema(name: str) -> dict:
    _, desc, props, req = TOOLS[name]
    if TENANT:
        desc = desc.replace("<tenant>", TENANT)
        props = {k: v.replace("<tenant>", TENANT) for k, v in props.items()}
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
                            "serverInfo": {"name": "vault", "version": "0.1"}})
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
            except (Refusal, TypeError, OSError, UnicodeDecodeError) as e:
                respond(id_, {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True})
        else:
            respond(id_, error={"code": -32601, "message": f"método desconhecido: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
