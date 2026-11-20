#!/usr/bin/env python3
"""MCP de exemplo de um add-on (stdio, só biblioteca padrão): uma ferramenta, `hello`."""
from __future__ import annotations

import json
import os
import sys


def respond(id_, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


TOOL = {
    "name": "hello",
    "description": "Responde com o nome do tenant. Prova que o MCP do add-on está ligado.",
    "inputSchema": {"type": "object", "properties": {}},
}


def main() -> int:
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if method == "initialize":
            respond(id_, {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "example_addon", "version": "0.1"}})
        elif id_ is None:
            continue
        elif method == "ping":
            respond(id_, {})
        elif method == "tools/list":
            respond(id_, {"tools": [TOOL]})
        elif method == "tools/call" and params.get("name") == "hello":
            tenant = os.environ.get("JARVIS_TENANT", "?")
            respond(id_, {"content": [{"type": "text", "text": f"olá do add-on de exemplo (tenant {tenant})"}]})
        else:
            respond(id_, error={"code": -32601, "message": f"desconhecido: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
