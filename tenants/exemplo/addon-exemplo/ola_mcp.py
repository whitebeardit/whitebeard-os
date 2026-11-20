#!/usr/bin/env python3
"""MCP de exemplo de um add-on (stdio, só biblioteca padrão): uma ferramenta, `ola`."""
from __future__ import annotations

import json
import os
import sys


def responder(id_, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


FERRAMENTA = {
    "name": "ola",
    "description": "Responde com o nome do tenant. Prova que o MCP do add-on está ligado.",
    "inputSchema": {"type": "object", "properties": {}},
}


def main() -> int:
    for linha in sys.stdin:
        try:
            req = json.loads(linha)
        except ValueError:
            continue
        metodo, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if metodo == "initialize":
            responder(id_, {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "exemplo_addon", "version": "0.1"}})
        elif id_ is None:
            continue
        elif metodo == "ping":
            responder(id_, {})
        elif metodo == "tools/list":
            responder(id_, {"tools": [FERRAMENTA]})
        elif metodo == "tools/call" and params.get("name") == "ola":
            tenant = os.environ.get("JARVIS_TENANT", "?")
            responder(id_, {"content": [{"type": "text", "text": f"olá do add-on de exemplo (tenant {tenant})"}]})
        else:
            responder(id_, error={"code": -32601, "message": f"desconhecido: {metodo}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
