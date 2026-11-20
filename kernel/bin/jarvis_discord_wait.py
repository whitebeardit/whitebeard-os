#!/usr/bin/env python3
"""jarvis_discord_wait.py — espera as mensagens ao bot do Discord de um assistente durante uma fatia.

Roda com o Python do venv do Hermes (tem `websockets`): o kernel não instala nada só para isto. Liga ao
gateway do Discord com intents comuns (sem as privilegiadas) e junta tudo o que chegar: várias pessoas podem
mandar na mesma fatia. Se o Discord pedir para reconectar,
conecta de novo na mesma fatia. Depois da primeira mensagem, espera mais alguns segundos (JUNTA_S) e devolve, para a
página mostrar logo e abrir a fatia seguinte. Em JSON:

  conversa direta  → {"person": "nome", "person_id": "123"}
  menção num canal → {"group": "#canal · servidor", "group_id": "456", "person": "nome", "person_id": "123"}
  chegou algo      → {"ok": true, "recognized": [<itens acima>], **<o primeiro item>}
  nada chegou      → {"ok": false, "error": "...", "timed_out": true}

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
from websockets.exceptions import ConnectionClosed

from jarvis_i18n import T

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
INTENTS = (1 << 0) | (1 << 9) | (1 << 12)  # GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES (nenhuma privilegiada)
GATHER_S = 6  # depois da primeira mensagem, quanto mais esperar por outras antes de devolver (e espaça os logins: 1 a cada 5 s)


def api_get(token: str, path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bot {token}", "User-Agent": "whitebeard-os (jarvis_discord_wait)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


FATAL = {4004: T("o Discord recusou o token (confira em Developer Portal → Bot → Reset Token)"),
          4010: T("o Discord recusou a conexão do bot (shard inválido)"), 4011: T("o bot precisa de sharding; fale com o suporte"),
          4012: T("versão da API do Discord inválida"), 4013: T("o Discord recusou as permissões do bot (intents inválidas)"),
          4014: T("o Discord recusou as permissões do bot")}


async def wait_messages(token: str, seconds: int, since: int) -> dict:
    """Junta as mensagens até o fim da fatia. Se o Discord pedir para reconectar (op 7), invalidar a sessão (op 9,
    por exemplo pelo limite de um login a cada 5 s, que vale também para o gateway do Hermes com o mesmo bot) ou
    derrubar a conexão por motivo não fatal, espera um pouco e conecta de novo na mesma fatia."""
    seq = None
    found: list[dict] = []
    deadline = time.monotonic() + seconds
    attempts = 0

    async def heartbeat(ws, interval_ms: float):
        while True:
            await asyncio.sleep(interval_ms / 1000)
            await ws.send(json.dumps({"op": 1, "d": seq}))

    def result() -> dict:
        if found:
            return {"ok": True, "recognized": found, **found[0]}
        return {"ok": False, "error": T("nenhuma mensagem chegou ao bot"), "timed_out": True}

    def keep(item: dict) -> None:
        nonlocal deadline
        key = (item.get("group_id"), item.get("person_id"))
        if all((a.get("group_id"), a.get("person_id")) != key for a in found):
            found.append(item)
        deadline = min(deadline, time.monotonic() + GATHER_S)

    async def one_connection() -> dict | None:
        """None = reconectar; dict = resultado final da fatia."""
        nonlocal seq
        me_id = None
        hb_task = None
        try:
            async with websockets.connect(GATEWAY, max_size=2**22) as ws:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return result()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return result()
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
                    elif op == 7:
                        return None
                    elif op == 9:
                        # sessão inválida: o Discord pede 1 a 5 s antes de um login novo
                        await asyncio.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
                        return None
                    elif t == "READY":
                        me_id = str(d["user"]["id"])
                    elif t == "MESSAGE_CREATE" and me_id:
                        author = d.get("author") or {}
                        if author.get("bot") or epoch(d.get("timestamp", "")) < since:
                            continue
                        name = str(author.get("global_name") or author.get("username") or "")
                        person = {"person": name, "person_id": str(author.get("id", ""))}
                        if not d.get("guild_id"):
                            keep(person)
                        elif any(str(m.get("id")) == me_id for m in d.get("mentions") or []):
                            channel_id = str(d["channel_id"])
                            label = f"#{channel_id}"
                            try:
                                ch = api_get(token, f"/channels/{channel_id}")
                                label = "#" + str(ch.get("name") or channel_id)
                                g = api_get(token, f"/guilds/{d['guild_id']}")
                                if g.get("name"):
                                    label += f" · {g['name']}"
                            except Exception:
                                pass
                            keep({"group": label, "group_id": channel_id, **person})
        except ConnectionClosed as e:
            if e.code in FATAL:
                return result() if found else {"ok": False, "error": FATAL[e.code]}
            return None
        except OSError:
            return None
        finally:
            if hb_task:
                hb_task.cancel()

    while time.monotonic() < deadline:
        r = await one_connection()
        if r is not None:
            return r
        attempts += 1
        # não martela o Discord: o limite é um login a cada 5 s
        await asyncio.sleep(min(2.0 * attempts, 5.0, max(0.0, deadline - time.monotonic())))
    return result()


async def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print(json.dumps({"ok": False, "error": T("sem token")}))
        return 1
    seconds = max(5, min(int(sys.argv[1]) if len(sys.argv) > 1 else 25, 300))
    since = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        res = await asyncio.wait_for(wait_messages(token, seconds, since), timeout=seconds + GATHER_S + 10)
    except asyncio.TimeoutError:
        res = {"ok": False, "error": T("nenhuma mensagem chegou ao bot"), "timed_out": True}
    except OSError as e:
        res = {"ok": False, "error": T("não consegui falar com o Discord ({error_class})", error_class=e.__class__.__name__)}
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
