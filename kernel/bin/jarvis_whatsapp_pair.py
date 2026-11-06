#!/usr/bin/env python3
"""jarvis_whatsapp_pair.py — pareia o WhatsApp de um assistente pela ponte do Hermes, sem terminal.

Roda a ponte (Node, Baileys) em modo de pareamento com saída JSON e vai escrevendo o estado num arquivo que a
página ASSISTENTES consulta: {"estado": "preparando|esperando|qr|conectado|erro|expirado", "qr": "...",
"telefone": "...", "nome": "...", "erro": "...", "atualizado_em": epoch}. Sai sozinho ao conectar, em erro ou
depois de `--tempo` segundos. Nunca escreve o conteúdo do QR em log.

Uso: jarvis_whatsapp_pair.py --ponte <dir da ponte> --sessao <dir da sessão> --estado <arquivo> --modo bot|self-chat [--tempo 300]
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


def escreve(path: Path, **campos) -> None:
    campos["atualizado_em"] = int(time.time())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(campos, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def telefone_de(user: object) -> str:
    """'número:dispositivo' + sufixo do WhatsApp -> só o número."""
    s = str((user or {}).get("id", "") if isinstance(user, dict) else user or "")
    return s.split(":", 1)[0].split("@", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ponte", required=True)
    ap.add_argument("--sessao", required=True)
    ap.add_argument("--estado", required=True)
    ap.add_argument("--modo", default="bot", choices=["bot", "self-chat"])
    ap.add_argument("--tempo", type=int, default=300)
    ap.add_argument("--porta", type=int, default=3999)
    a = ap.parse_args()
    estado = Path(a.estado)
    estado.parent.mkdir(parents=True, exist_ok=True)
    sessao = Path(a.sessao)
    sessao.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    if not node:
        escreve(estado, estado="erro", erro="node não encontrado")
        return 1
    escreve(estado, estado="preparando", pid=os.getpid())
    env = dict(os.environ, WHATSAPP_MODE=a.modo, WHATSAPP_DM_POLICY="pairing")
    cmd = [node, str(Path(a.ponte) / "bridge.js"), "--pair-only", "--pair-json", "--session", str(sessao), "--mode", a.modo, "--port", str(a.porta)]
    proc = subprocess.Popen(cmd, cwd=a.ponte, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    fim = time.time() + a.tempo

    cancelado = {"sim": False}

    def encerra(*_):
        if proc.poll() is None:
            proc.terminate()

    def cancela(*_):
        cancelado["sim"] = True
        escreve(estado, estado="cancelado")
        encerra()
    signal.signal(signal.SIGTERM, cancela)

    escreve(estado, estado="esperando", pid=os.getpid())
    rc = 0
    try:
        while True:
            if time.time() > fim:
                escreve(estado, estado="expirado", erro="o QR não foi lido em 5 minutos; gere outro")
                encerra()
                break
            linha = proc.stdout.readline() if proc.stdout else ""
            if not linha:
                if proc.poll() is not None:
                    atual = json.loads(estado.read_text(encoding="utf-8")) if estado.exists() else {}
                    if cancelado["sim"]:
                        break
                    if atual.get("estado") not in ("conectado", "erro", "expirado", "cancelado"):
                        escreve(estado, estado="erro", erro="a ponte do WhatsApp encerrou antes de parear (veja o registro do Hermes)")
                        rc = 1
                    break
                time.sleep(0.2)
                continue
            try:
                ev = json.loads(linha.strip())
            except ValueError:
                continue
            tipo = ev.get("event")
            if tipo == "qr" and ev.get("qr"):
                escreve(estado, estado="qr", qr=ev["qr"], pid=os.getpid())
            elif tipo == "connected":
                user = ev.get("user") or {}
                escreve(estado, estado="conectado", telefone=telefone_de(user), nome=str(user.get("name") or "") if isinstance(user, dict) else "")
                encerra()
                break
            elif tipo == "error":
                motivo = str(ev.get("error") or ev.get("reason") or "erro")
                if motivo == "logged_out":
                    motivo = "o WhatsApp desconectou este aparelho; pareie de novo"
                escreve(estado, estado="erro", erro=motivo)
                rc = 1
                encerra()
                break
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return rc


if __name__ == "__main__":
    sys.exit(main())
