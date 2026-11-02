#!/usr/bin/env python3
"""jarvis_setup.py — configuração depois da instalação, sem terminal interativo.

Quem usa: o JARVIS Web (página PRIMEIROS PASSOS) e o bootstrap.sh (--telegram). Só biblioteca padrão,
para rodar com o python3 do sistema a partir de um serviço. Cada comando imprime UMA linha de JSON.

Segredos nunca passam por argumento nem saem na resposta: entram por stdin e ficam em
$JARVIS_HOME/secrets.env (modo 600). O estado diz só se cada um está preenchido.

  jarvis_setup.py --home ~/.jarvis status
  jarvis_setup.py --home ~/.jarvis set-secret NOME            valor em stdin; vazio limpa
  jarvis_setup.py --home ~/.jarvis telegram-token             token em stdin; valida em getMe e guarda
  jarvis_setup.py --home ~/.jarvis telegram-wait [--seconds N] [--since EPOCH]
                                                              espera a primeira mensagem ao bot e guarda o chat
  jarvis_setup.py --home ~/.jarvis telegram-off               a pessoa não quer Telegram (não perguntar de novo)
  jarvis_setup.py --home ~/.jarvis done                       marca os primeiros passos como concluídos
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# o kernel gera ou deriva estes; o Telegram tem o seu próprio passo
HIDDEN = {"GBRAIN_MCP_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"}


def out(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0 if obj.get("ok", True) else 1


def read_env(path: Path) -> dict[str, str]:
    """NOME=valor de um arquivo gerado pelo kernel (valores entre aspas simples ou nus)."""
    vals: dict[str, str] = {}
    if not path.is_file():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            parts = shlex.split(v)
        except ValueError:
            parts = [v]
        vals[k.strip()] = parts[0] if parts else ""
    return vals


def set_secret(path: Path, name: str, value: str) -> None:
    """Mesma regra do bootstrap: substitui a linha NOME=..., ou acrescenta; valor entre aspas simples."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    quoted = ("'" + value.replace("'", "'\\''") + "'") if value else ""
    new_line = f"{name}={quoted}"
    text2, n = re.subn(r"(?m)^" + re.escape(name) + r"=.*$", lambda _: new_line, text)
    if not n:
        text2 = text + ("" if text.endswith("\n") or not text else "\n") + new_line + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text2, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def stdin_value() -> str:
    data = sys.stdin.read()
    # uma linha: o \n final vem do terminal ou do echo, não é parte do valor
    if data.endswith("\n"):
        data = data[:-1]
    if data.endswith("\r"):
        data = data[:-1]
    return data.strip()


def declared_secrets(home: Path) -> list[dict]:
    try:
        return json.loads((home / "render" / "secrets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- status
def cmd_status(home: Path) -> int:
    profile = read_env(home / "profile.env")
    secrets_path = home / "secrets.env"
    filled = {k: bool(v) for k, v in read_env(secrets_path).items()}
    roles = [r for r in profile.get("JARVIS_ROLE", "").split(",") if r]
    declared = declared_secrets(home)
    if not declared:
        # instalação de um kernel sem secrets.json: só os nomes do secrets.env
        declared = [{"nome": k} for k in filled]
    segredos = []
    for s in declared:
        nome = s.get("nome", "")
        if not nome or nome in HIDDEN or s.get("gerado"):
            continue
        segredos.append({
            "nome": nome,
            "descricao": s.get("descricao", ""),
            "obter": s.get("obter", ""),
            "obrigatorio": bool(s.get("obrigatorio")),
            "implicito": bool(s.get("implicito")),
            "preenchido": filled.get(nome, False),
        })
    segredos.sort(key=lambda s: (not s["obrigatorio"], s["preenchido"], s["nome"]))
    bot = ""
    try:
        bot = (home / "telegram.bot").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return out({
        "ok": True,
        "tenant": profile.get("JARVIS_TENANT", ""),
        "papeis": roles,
        "dados": profile.get("JARVIS_DATA", ""),
        "vault": profile.get("VAULT_PATH", ""),
        "telegram": {
            "disponivel": "core" in roles,
            "token": filled.get("TELEGRAM_BOT_TOKEN", False),
            "chat": filled.get("TELEGRAM_HOME_CHANNEL", False),
            "recusado": (home / "telegram.decisao").is_file(),
            "bot": bot,
        },
        "segredos": segredos,
        "concluido": (home / "setup.done").is_file(),
    })


# ---------------------------------------------------------------- set-secret
def cmd_set_secret(home: Path, name: str) -> int:
    if not NAME_RE.match(name or ""):
        return out({"ok": False, "erro": "nome de segredo inválido"})
    known = {s.get("nome") for s in declared_secrets(home)} | set(read_env(home / "secrets.env"))
    if name not in known or name in HIDDEN:
        return out({"ok": False, "erro": f"{name} não é um segredo deste tenant"})
    value = stdin_value()
    if "\n" in value:
        return out({"ok": False, "erro": "o valor tem mais de uma linha"})
    set_secret(home / "secrets.env", name, value)
    return out({"ok": True, "nome": name, "preenchido": bool(value)})


# ---------------------------------------------------------------- telegram
class TelegramError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(msg)
        self.code = code


class TelegramConnection(http.client.HTTPSConnection):
    """HTTPS que tenta IPv4 antes de IPv6, com conexão curta por endereço.

    Em muitas redes domésticas o IPv6 é anunciado mas não chega à internet: o Python tentaria o
    endereço IPv6 primeiro e só desistiria no fim do timeout inteiro (35 s parado antes de responder).
    Depois de conectado, o timeout volta ao valor pedido (o long-poll do getUpdates precisa dele).
    """

    CONNECT_TIMEOUT = 6

    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda i: i[0] != socket.AF_INET)
        err: OSError | None = None
        sock = None
        for fam, typ, proto, _, addr in infos:
            s = socket.socket(fam, typ, proto)
            s.settimeout(self.CONNECT_TIMEOUT)
            try:
                s.connect(addr)
                sock = s
                break
            except OSError as e:
                err = e
                s.close()
        if sock is None:
            raise err or OSError("sem rota para api.telegram.org")
        sock.settimeout(self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def tg_api(token: str, method: str, http_timeout: int = 40, **q):
    """Chama a API do bot; `q` vira query string (o `timeout` dela é o long-poll do Telegram)."""
    path = f"/bot{token}/{method}"
    if q:
        path += "?" + urllib.parse.urlencode(q)
    conn = TelegramConnection("api.telegram.org", timeout=http_timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise TelegramError(resp.status, f"HTTP {resp.status}")
        return json.loads(body)["result"]
    except TelegramError:
        raise
    except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
        raise TelegramError(None, str(e)) from e
    finally:
        conn.close()


def cmd_telegram_token(home: Path) -> int:
    token = stdin_value()
    if not token:
        return out({"ok": False, "erro": "token vazio"})
    try:
        me = tg_api(token, "getMe", http_timeout=20)
    except TelegramError as e:
        if e.code in (401, 404):
            return out({"ok": False, "erro": "o Telegram recusou o token (confira em @BotFather)"})
        return out({"ok": False, "erro": "não consegui falar com o Telegram; confira a internet e tente de novo"})
    username = str(me.get("username") or "")
    set_secret(home / "secrets.env", "TELEGRAM_BOT_TOKEN", token)
    (home / "telegram.bot").write_text(username + "\n", encoding="utf-8")
    try:
        (home / "telegram.decisao").unlink()
    except OSError:
        pass
    return out({"ok": True, "bot": username})


def hermes_bin() -> str | None:
    return shutil.which("hermes") or shutil.which("hermes", path=str(Path.home() / ".local" / "bin"))


def hermes_gateway(action: str) -> None:
    h = hermes_bin()
    if not h:
        return
    try:
        subprocess.run([h, "gateway", action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_telegram_wait(home: Path, seconds: int, since: int) -> int:
    token = read_env(home / "secrets.env").get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return out({"ok": False, "erro": "sem token do bot"})
    seconds = max(5, min(seconds, 300))
    deadline = time.time() + seconds
    offset = 0
    stopped_gateway = False
    try:
        # o backlog pode ter mensagem de qualquer pessoa: só vale o que chegar depois de `since`
        last = tg_api(token, "getUpdates", offset=-1, timeout=0)
        if last:
            offset = last[-1]["update_id"] + 1
            # a última pode ser a própria mensagem do dono, mandada antes de este comando começar
            m = last[-1].get("message") or {}
            if m.get("date", 0) >= since and (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    except TelegramError as e:
        if e.code != 409:
            return out({"ok": False, "erro": "não consegui falar com o Telegram"})
    while time.time() < deadline:
        try:
            ups = tg_api(token, "getUpdates", http_timeout=35, offset=offset, allowed_updates='["message"]',
                         timeout=min(25, max(1, int(deadline - time.time()))))
        except TelegramError as e:
            if e.code == 409 and not stopped_gateway:
                # outro leitor (o gateway do Hermes com este token) disputa as mensagens: para e devolve no fim
                hermes_gateway("stop")
                stopped_gateway = True
                time.sleep(2)
                continue
            if e.code == 409:
                time.sleep(2)
                continue
            if stopped_gateway:
                hermes_gateway("start")
            return out({"ok": False, "erro": "o Telegram não entregou mensagens; tente de novo"})
        for u in ups:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("date", 0) < since:
                continue
            if (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True})


def finish_wait(home: Path, token: str, offset: int, m: dict, stopped_gateway: bool) -> int:
    try:
        tg_api(token, "getUpdates", offset=offset, timeout=0)  # marca como lida: o gateway não responde a ela de novo
    except TelegramError:
        pass
    sender = m.get("from") or {}
    set_secret(home / "secrets.env", "TELEGRAM_HOME_CHANNEL", str(sender["id"]))
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": True, "chat": True, "nome": str(sender.get("first_name") or "")})


def cmd_telegram_off(home: Path) -> int:
    (home / "telegram.decisao").write_text("nao\n", encoding="utf-8")
    return out({"ok": True})


def cmd_done(home: Path) -> int:
    (home / "setup.done").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return out({"ok": True})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("set-secret")
    p.add_argument("name")
    sub.add_parser("telegram-token")
    p = sub.add_parser("telegram-wait")
    p.add_argument("--seconds", type=int, default=170)
    p.add_argument("--since", type=int, default=0)
    sub.add_parser("telegram-off")
    sub.add_parser("done")
    args = ap.parse_args()
    home = Path(args.home).expanduser()
    if not (home / "profile.env").is_file():
        return out({"ok": False, "erro": f"instalação não encontrada em {home}"})
    if args.cmd == "status":
        return cmd_status(home)
    if args.cmd == "set-secret":
        return cmd_set_secret(home, args.name)
    if args.cmd == "telegram-token":
        return cmd_telegram_token(home)
    if args.cmd == "telegram-wait":
        return cmd_telegram_wait(home, args.seconds, args.since or int(time.time()))
    if args.cmd == "telegram-off":
        return cmd_telegram_off(home)
    if args.cmd == "done":
        return cmd_done(home)
    return 2


if __name__ == "__main__":
    sys.exit(main())
