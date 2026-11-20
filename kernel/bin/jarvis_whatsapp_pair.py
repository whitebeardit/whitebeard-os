#!/usr/bin/env python3
"""jarvis_whatsapp_pair.py — pareia o WhatsApp de um assistente pela ponte do Hermes, sem terminal.

Roda a ponte (Node, Baileys) em modo de pareamento com saída JSON e vai escrevendo o estado num arquivo que a
página ASSISTENTES consulta: {"state": "preparing|waiting|qr|connected|error|expired", "qr": "...",
"phone": "...", "name": "...", "error": "...", "updated_at": epoch}. Sai sozinho ao conectar, em erro ou
depois de `--timeout` segundos. Nunca escreve o conteúdo do QR em log.

Uso: jarvis_whatsapp_pair.py --bridge <dir da ponte> --session <dir da sessão> --state <arquivo> --mode bot|self-chat [--timeout 300]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from jarvis_i18n import T


def write_state(path: Path, **fields) -> None:
    fields["updated_at"] = int(time.time())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def phone_of(user: object) -> str:
    """'número:dispositivo' + sufixo do WhatsApp -> só o número."""
    s = str((user or {}).get("id", "") if isinstance(user, dict) else user or "")
    return s.split(":", 1)[0].split("@", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--mode", default="bot", choices=["bot", "self-chat"])
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--port", type=int, default=3999)
    a = ap.parse_args()
    state = Path(a.state)
    state.parent.mkdir(parents=True, exist_ok=True)
    session = Path(a.session)
    session.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    if not node:
        write_state(state, state="error", error=T("node não encontrado"))
        return 1
    write_state(state, state="preparing", pid=os.getpid())
    env = dict(os.environ, WHATSAPP_MODE=a.mode, WHATSAPP_DM_POLICY="pairing")
    cmd = [node, str(Path(a.bridge) / "bridge.js"), "--pair-only", "--pair-json", "--session", str(session), "--mode", a.mode, "--port", str(a.port)]
    proc = subprocess.Popen(cmd, cwd=a.bridge, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    deadline = time.time() + a.timeout

    cancelled = {"flag": False}

    def stop_bridge(*_):
        if proc.poll() is None:
            proc.terminate()

    def on_cancel(*_):
        cancelled["flag"] = True
        write_state(state, state="cancelled")
        stop_bridge()
    signal.signal(signal.SIGTERM, on_cancel)

    write_state(state, state="waiting", pid=os.getpid())
    rc = 0
    try:
        while True:
            if time.time() > deadline:
                write_state(state, state="expired", error=T("o QR não foi lido em 5 minutos; gere outro"))
                stop_bridge()
                break
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    current = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
                    if cancelled["flag"]:
                        break
                    if current.get("state") not in ("connected", "error", "expired", "cancelled"):
                        write_state(state, state="error", error=T("a ponte do WhatsApp encerrou antes de parear (veja o registro do Hermes)"))
                        rc = 1
                    break
                time.sleep(0.2)
                continue
            try:
                ev = json.loads(line.strip())
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "qr" and ev.get("qr"):
                write_state(state, state="qr", qr=ev["qr"], pid=os.getpid())
            elif kind == "connected":
                user = ev.get("user") or {}
                write_state(state, state="connected", phone=phone_of(user), name=str(user.get("name") or "") if isinstance(user, dict) else "")
                stop_bridge()
                break
            elif kind == "error":
                reason = str(ev.get("error") or ev.get("reason") or "error")
                if reason == "logged_out":
                    reason = T("o WhatsApp desconectou este aparelho; pareie de novo")
                write_state(state, state="error", error=reason)
                rc = 1
                stop_bridge()
                break
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return rc


if __name__ == "__main__":
    sys.exit(main())
