#!/usr/bin/env python3
"""jarvis_discord_wait.py — espera as mensagens ao bot do Discord de um assistente durante uma fatia.

Roda com o Python do venv do Hermes (tem `websockets`): o kernel não instala nada só para isto. Liga ao
gateway do Discord com intents comuns (sem as privilegiadas) e junta tudo o que chegar: várias pessoas podem
mandar na mesma fatia. Depois da primeira mensagem, espera mais alguns segundos (JUNTA_S) e devolve, para a
página mostrar logo e abrir a fatia seguinte. Em JSON:

  conversa direta  → {"pessoa": "nome", "pessoa_id": "123"}
  menção num canal → {"grupo": "#canal · servidor", "grupo_id": "456", "pessoa": "nome", "pessoa_id": "123"}
  chegou algo      → {"ok": true, "reconhecidos": [<itens acima>], **<o primeiro item>}
  nada chegou      → {"ok": false, "erro": "...", "tempo_esgotado": true}

Uso: jarvis_discord_wait.py <segundos> <desde-epoch>   (o token vem pela variável DISCORD_BOT_TOKEN)
Nunca imprime o token.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

import websockets

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
INTENTS = (1 << 0) | (1 << 9) | (1 << 12)  # GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES (nenhuma privilegiada)
JUNTA_S = 3  # depois da primeira mensagem, quanto mais esperar por outras antes de devolver


def api_get(token: str, path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bot {token}", "User-Agent": "whitebeard-os (jarvis_discord_wait)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


async def esperar(token: str, seconds: int, since: int) -> dict:
    me_id = None
    seq = None
    hb_task = None
    achados: list[dict] = []
    deadline = time.monotonic() + seconds

    async def heartbeat(ws, interval_ms: float):
        while True:
            await asyncio.sleep(interval_ms / 1000)
            await ws.send(json.dumps({"op": 1, "d": seq}))

    def resultado() -> dict:
        if achados:
            return {"ok": True, "reconhecidos": achados, **achados[0]}
        return {"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True}

    def guarda(item: dict) -> None:
        nonlocal deadline
        chave = (item.get("grupo_id"), item.get("pessoa_id"))
        if all((a.get("grupo_id"), a.get("pessoa_id")) != chave for a in achados):
            achados.append(item)
        deadline = min(deadline, time.monotonic() + JUNTA_S)

    try:
        async with websockets.connect(GATEWAY, max_size=2**22) as ws:
            while True:
                falta = deadline - time.monotonic()
                if falta <= 0:
                    return resultado()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=falta)
                except asyncio.TimeoutError:
                    return resultado()
                msg = json.loads(raw)
                op, t, d = msg.get("op"), msg.get("t"), msg.get("d")
                if msg.get("s") is not None:
                    seq = msg["s"]
                if op == 10:
                    hb_task = asyncio.create_task(heartbeat(ws, d["heartbeat_interval"]))
                    await ws.send(json.dumps({"op": 2, "d": {"token": token, "intents": INTENTS,
                                                              "properties": {"os": sys.platform, "browser": "whitebeard-os", "device": "whitebeard-os"}}}))
                elif op == 1:
                    await ws.send(json.dumps({"op": 1, "d": seq}))
                elif op == 7 or op == 9:
                    return resultado() if achados else {"ok": False, "erro": "o Discord pediu para reconectar; tente de novo"}
                elif t == "READY":
                    me_id = str(d["user"]["id"])
                elif t == "MESSAGE_CREATE" and me_id:
                    autor = d.get("author") or {}
                    if autor.get("bot") or epoch(d.get("timestamp", "")) < since:
                        continue
                    nome = str(autor.get("global_name") or autor.get("username") or "")
                    pessoa = {"pessoa": nome, "pessoa_id": str(autor.get("id", ""))}
                    if not d.get("guild_id"):
                        guarda(pessoa)
                    elif any(str(m.get("id")) == me_id for m in d.get("mentions") or []):
                        canal_id = str(d["channel_id"])
                        rotulo = f"#{canal_id}"
                        try:
                            ch = api_get(token, f"/channels/{canal_id}")
                            rotulo = "#" + str(ch.get("name") or canal_id)
                            g = api_get(token, f"/guilds/{d['guild_id']}")
                            if g.get("name"):
                                rotulo += f" · {g['name']}"
                        except Exception:
                            pass
                        guarda({"grupo": rotulo, "grupo_id": canal_id, **pessoa})
    except websockets.exceptions.ConnectionClosed as e:
        if achados:
            return resultado()
        if e.code == 4004:
            return {"ok": False, "erro": "o Discord recusou o token (confira em Developer Portal → Bot → Reset Token)"}
        if e.code == 4014:
            return {"ok": False, "erro": "o Discord recusou as permissões do bot"}
        return {"ok": False, "erro": f"a conexão com o Discord caiu ({e.code}); tente de novo"}
    finally:
        if hb_task:
            hb_task.cancel()


async def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print(json.dumps({"ok": False, "erro": "sem token"}))
        return 1
    seconds = max(5, min(int(sys.argv[1]) if len(sys.argv) > 1 else 25, 300))
    since = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        res = await asyncio.wait_for(esperar(token, seconds, since), timeout=seconds + JUNTA_S + 10)
    except asyncio.TimeoutError:
        res = {"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True}
    except OSError as e:
        res = {"ok": False, "erro": f"não consegui falar com o Discord ({e.__class__.__name__})"}
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
