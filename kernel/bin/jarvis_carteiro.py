#!/usr/bin/env python3
"""jarvis_carteiro.py — o carteiro do Escritório: avisa no canal de origem quando uma tarefa foi entregue,
parou numa pendência ou foi recusada, e é o relógio das rotinas (cria a tarefa de cada rotina no horário dela).
Roda a cada minuto (serviço do usuário) e lembra o que já avisou e disparou em $JARVIS_HOME/escritorio/carteiro.json.
Só biblioteca padrão; manda pelo `hermes send` (credenciais do Hermes; `hermes -p <assistente> send` para publicar
pelo bot de um assistente).

Uso: jarvis_carteiro.py --home <JARVIS_HOME> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100/api").rstrip("/")
ESTADOS = {"done": "ENTREGUE", "cancelled": "CANCELADA"}


def api(path: str):
    req = urllib.request.Request(API + path, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def lista(d, chave):
    return d if isinstance(d, list) else (d or {}).get(chave, [])


def origem_de(desc: str) -> str:
    m = re.search(r"\[origem:\s*([^\]]+)\]", desc or "")
    return m.group(1).strip() if m else "telegram"


def ultimo_comentario(issue_id: str) -> str:
    """O último comentário do cargo (ENTREGA/PENDÊNCIA/RECUSADO); senão, o último que não é prompt do Paperclip."""
    cs = lista(api(f"/issues/{issue_id}/comments"), "comments")
    cs.sort(key=lambda c: c.get("createdAt") or "")
    corpos = [(c.get("body") or "").strip() for c in cs]
    for b in reversed(corpos):
        if b.upper().startswith(("ENTREGA:", "PENDÊNCIA:", "RECUSADO", "APROVADO")):
            return b
    for b in reversed(corpos):
        if b and not b.startswith(("Query:", "Paperclip ")):
            return b
    return ""


def mensagem(it: dict, comentario: str) -> str:
    estado = it.get("status")
    numero = it.get("identifier") or it.get("id", "")[:8]
    titulo = it.get("title") or ""
    if estado == "done":
        corpo = comentario[len("ENTREGA:"):].strip() if comentario.upper().startswith("ENTREGA:") else comentario
        return f"✅ Entregue: {titulo} ({numero})\n{corpo}".strip()
    if estado == "blocked":
        corpo = comentario[len("PENDÊNCIA:"):].strip() if comentario.upper().startswith("PENDÊNCIA:") else comentario
        return f"⏸ Preciso que você decida: {titulo} ({numero})\n{corpo}\nResponda \"aprovar {numero}\" ou \"recusar {numero}\". Para pedir um ajuste: \"aprovar {numero} com a nota: o que mudar\"."
    corpo = comentario[len("RECUSADO:"):].strip() if comentario.upper().startswith("RECUSADO") else comentario
    return f"✖ Cancelada: {titulo} ({numero})\n{corpo}".strip()


def destino_assistente(home: Path, nome: str) -> tuple[str, str] | None:
    """Para onde um assistente publica: (perfil, alvo do hermes send) = o primeiro grupo/canal onde ele responde.
    O canal vem do catálogo do render; os ids dos grupos, do .env do perfil dele (não são segredos)."""
    canal = "telegram"
    try:
        for a in json.loads((home / "render" / "hermes" / "assistentes.json").read_text(encoding="utf-8")):
            if a.get("nome") == nome:
                canal = a.get("canal") or "telegram"
                break
        else:
            return None
    except (OSError, ValueError):
        return None
    hermes_home = Path.home() / ".hermes"
    try:
        for line in (home / "profile.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("HERMES_HOME="):
                hermes_home = Path(line.split("=", 1)[1].strip().strip("'\""))
    except OSError:
        pass
    chave = {"telegram": "TELEGRAM_GROUP_ALLOWED_CHATS", "discord": "DISCORD_ALLOWED_CHANNELS"}.get(canal)
    if not chave:
        return None
    try:
        for line in (hermes_home / "profiles" / nome / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(chave + "="):
                ids = [x.strip() for x in line.split("=", 1)[1].split(",") if x.strip()]
                if ids:
                    return nome, f"{canal}:{ids[0]}"
    except OSError:
        pass
    return None


def enviar(destino: str, texto: str, dry: bool, log, home: Path | None = None) -> bool:
    perfil = ""
    if destino.startswith("assistente:") and home is not None:
        alvo = destino_assistente(home, destino.split(":", 1)[1].strip())
        if not alvo:
            log(f"[carteiro] não sei onde o {destino} publica (assistente sem grupo reconhecido); aviso vai para o telegram do dono")
            destino = "telegram"
        else:
            perfil, destino = alvo
    if dry:
        log(f"[carteiro] (dry-run) para {destino}{' pelo ' + perfil if perfil else ''}: {texto[:120]!r}")
        return True
    hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    cmd = [hermes] + (["-p", perfil] if perfil else []) + ["send", "--to", destino, "-q", texto]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"[carteiro] falha ao enviar para {destino}: {e.__class__.__name__}")
        return False
    if r.returncode != 0:
        log(f"[carteiro] hermes send falhou para {destino}: {(r.stderr or r.stdout).strip()[:160]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    # este processo escreve em chats e no registro, não na interface: os erros do jarvis_rotinas seguem em português
    os.environ["JARVIS_IDIOMA"] = "pt-BR"
    home = Path(a.home).expanduser()
    pasta = home / "escritorio"
    pasta.mkdir(parents=True, exist_ok=True)
    estado_path = pasta / "carteiro.json"
    try:
        estado = json.loads(estado_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        estado = {"avisadas": {}, "inicio": int(time.time())}
    avisadas = estado.setdefault("avisadas", {})
    log = lambda m: print(time.strftime("%Y-%m-%d %H:%M:%S"), m, flush=True)  # noqa: E731
    # o relógio das rotinas: cria a tarefa de cada rotina no horário dela (a entrega volta pelo caminho normal)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_rotinas
        if jarvis_rotinas.agendar(home, estado, log=log, dry=a.dry_run):
            estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    except ImportError:
        log("[carteiro] jarvis_rotinas.py ausente: rotinas não agendadas")
    try:
        comp = lista(api("/companies"), "companies")
        if not comp:
            return 0
        issues = lista(api(f"/companies/{comp[0]['id']}/issues"), "issues")
    except Exception as e:  # Paperclip fora do ar: tenta no próximo minuto
        log(f"[carteiro] escritório indisponível ({e.__class__.__name__})")
        return 0
    inicio = estado.get("inicio") or 0
    for it in issues:
        st = it.get("status")
        # pendência: uma confirmação aberta na tarefa (o cargo pediu uma decisão)
        if st in ("in_progress", "blocked", "todo"):
            try:
                pend = [i for i in lista(api(f"/issues/{it['id']}/interactions"), "interactions")
                        if i.get("kind") == "request_confirmation" and i.get("status") in ("pending", "open", "awaiting")]
            except Exception:
                pend = []
            for p_ in pend:
                chave = f"{it['id']}:{p_['id']}"
                if avisadas.get(chave):
                    continue
                pergunta = ((p_.get("payload") or {}).get("prompt") or "").strip()
                numero = it.get("identifier") or it.get("id", "")[:8]
                texto = f"⏸ Preciso que você decida: {it.get('title') or ''} ({numero})\n{pergunta}\nResponda \"aprovar {numero}\" ou \"recusar {numero}\". Para pedir um ajuste: \"aprovar {numero} com a nota: o que mudar\"."
                if enviar(origem_de(it.get("description") or ""), texto, a.dry_run, log, home):
                    avisadas[chave] = "pendencia"
                    log(f"[carteiro] avisado: {numero} PENDÊNCIA")
        if st not in ESTADOS or avisadas.get(it["id"]) == st:
            continue
        # tarefas de antes do carteiro existir não geram aviso retroativo
        atualizada = it.get("updatedAt") or ""
        try:
            ts = time.mktime(time.strptime(atualizada[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            ts = time.time()
        if ts < inicio - 60:
            avisadas[it["id"]] = st
            continue
        try:
            texto = mensagem(it, ultimo_comentario(it["id"]))
        except Exception as e:
            log(f"[carteiro] não li a tarefa {it.get('identifier')}: {e.__class__.__name__}")
            continue
        if enviar(origem_de(it.get("description") or ""), texto, a.dry_run, log, home):
            avisadas[it["id"]] = st
            log(f"[carteiro] avisado: {it.get('identifier')} {ESTADOS[st]}")
    estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
