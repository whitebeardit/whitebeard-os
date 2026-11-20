#!/usr/bin/env python3
"""jarvis_courier.py — o carteiro do Escritório: avisa no canal de origem quando uma tarefa foi entregue,
parou numa pendência ou foi recusada, e é o relógio das rotinas (cria a tarefa de cada rotina no horário dela).
Roda a cada minuto (serviço do usuário) e lembra o que já avisou e disparou em $JARVIS_HOME/office/courier.json.
Só biblioteca padrão; manda pelo `hermes send` (credenciais do Hermes; `hermes -p <assistente> send` para publicar
pelo bot de um assistente).

Uso: jarvis_courier.py --home <JARVIS_HOME> [--dry-run]
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
STATES = {"done": "ENTREGUE", "cancelled": "CANCELADA"}


def api(path: str):
    req = urllib.request.Request(API + path, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def as_list(d, key):
    return d if isinstance(d, list) else (d or {}).get(key, [])


def origin_of(desc: str) -> str:
    m = re.search(r"\[origin:\s*([^\]]+)\]", desc or "")
    return m.group(1).strip() if m else "telegram"


def last_comment(issue_id: str) -> str:
    """O último comentário do cargo (ENTREGA/PENDÊNCIA/REJECTED); senão, o último que não é prompt do Paperclip."""
    cs = as_list(api(f"/issues/{issue_id}/comments"), "comments")
    cs.sort(key=lambda c: c.get("createdAt") or "")
    bodies = [(c.get("body") or "").strip() for c in cs]
    for b in reversed(bodies):
        if b.upper().startswith(("DELIVERY:", "PENDING:", "REJECTED", "APPROVED")):
            return b
    for b in reversed(bodies):
        if b and not b.startswith(("Query:", "Paperclip ")):
            return b
    return ""


def message(it: dict, comment_text: str) -> str:
    state = it.get("status")
    number = it.get("identifier") or it.get("id", "")[:8]
    title = it.get("title") or ""
    if state == "done":
        body = comment_text[len("DELIVERY:"):].strip() if comment_text.upper().startswith("DELIVERY:") else comment_text
        return f"✅ Entregue: {title} ({number})\n{body}".strip()
    if state == "blocked":
        body = comment_text[len("PENDING:"):].strip() if comment_text.upper().startswith("PENDING:") else comment_text
        return f"⏸ Preciso que você decida: {title} ({number})\n{body}\nResponda \"aprovar {number}\" ou \"recusar {number}\". Para pedir um ajuste: \"aprovar {number} com a nota: o que mudar\"."
    body = comment_text[len("REJECTED:"):].strip() if comment_text.upper().startswith("REJECTED") else comment_text
    return f"✖ Cancelada: {title} ({number})\n{body}".strip()


def assistant_destination(home: Path, name: str) -> tuple[str, str] | None:
    """Para onde um assistente publica: (perfil, alvo do hermes send) = o primeiro grupo/canal onde ele responde.
    O canal vem do catálogo do render; os ids dos grupos, do .env do perfil dele (não são segredos)."""
    channel = "telegram"
    try:
        for a in json.loads((home / "render" / "hermes" / "assistants.json").read_text(encoding="utf-8")):
            if a.get("name") == name:
                channel = a.get("channel") or "telegram"
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
    key = {"telegram": "TELEGRAM_GROUP_ALLOWED_CHATS", "discord": "DISCORD_ALLOWED_CHANNELS"}.get(channel)
    if not key:
        return None
    try:
        for line in (hermes_home / "profiles" / name / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                ids = [x.strip() for x in line.split("=", 1)[1].split(",") if x.strip()]
                if ids:
                    return name, f"{channel}:{ids[0]}"
    except OSError:
        pass
    return None


def send(destination: str, text: str, dry: bool, log, home: Path | None = None) -> bool:
    profile = ""
    if destination.startswith("assistant:") and home is not None:
        target = assistant_destination(home, destination.split(":", 1)[1].strip())
        if not target:
            log(f"[carteiro] não sei onde o {destination} publica (assistente sem grupo reconhecido); aviso vai para o telegram do dono")
            destination = "telegram"
        else:
            profile, destination = target
    if dry:
        log(f"[carteiro] (dry-run) para {destination}{' pelo ' + profile if profile else ''}: {text[:120]!r}")
        return True
    hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    cmd = [hermes] + (["-p", profile] if profile else []) + ["send", "--to", destination, "-q", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"[carteiro] falha ao enviar para {destination}: {e.__class__.__name__}")
        return False
    if r.returncode != 0:
        log(f"[carteiro] hermes send falhou para {destination}: {(r.stderr or r.stdout).strip()[:160]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    # este processo escreve em chats e no registro, não na interface: os erros do jarvis_routines seguem em português
    os.environ["JARVIS_LANGUAGE"] = "pt-BR"
    home = Path(a.home).expanduser()
    folder = home / "office"
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "courier.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"notified": {}, "started_at": int(time.time())}
    notified = state.setdefault("notified", {})
    log = lambda m: print(time.strftime("%Y-%m-%d %H:%M:%S"), m, flush=True)  # noqa: E731
    # o relógio das rotinas: cria a tarefa de cada rotina no horário dela (a entrega volta pelo caminho normal)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_routines
        if jarvis_routines.schedule(home, state, log=log, dry=a.dry_run):
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except ImportError:
        log("[carteiro] jarvis_routines.py ausente: rotinas não agendadas")
    try:
        comp = as_list(api("/companies"), "companies")
        if not comp:
            return 0
        issues = as_list(api(f"/companies/{comp[0]['id']}/issues"), "issues")
    except Exception as e:  # Paperclip fora do ar: tenta no próximo minuto
        log(f"[carteiro] escritório indisponível ({e.__class__.__name__})")
        return 0
    started_at = state.get("started_at") or 0
    for it in issues:
        st = it.get("status")
        # pendência: uma confirmação aberta na tarefa (o cargo pediu uma decisão)
        if st in ("in_progress", "blocked", "todo"):
            try:
                pending = [i for i in as_list(api(f"/issues/{it['id']}/interactions"), "interactions")
                        if i.get("kind") == "request_confirmation" and i.get("status") in ("pending", "open", "awaiting")]
            except Exception:
                pending = []
            for p_ in pending:
                key = f"{it['id']}:{p_['id']}"
                if notified.get(key):
                    continue
                question = ((p_.get("payload") or {}).get("prompt") or "").strip()
                number = it.get("identifier") or it.get("id", "")[:8]
                text = f"⏸ Preciso que você decida: {it.get('title') or ''} ({number})\n{question}\nResponda \"aprovar {number}\" ou \"recusar {number}\". Para pedir um ajuste: \"aprovar {number} com a nota: o que mudar\"."
                if send(origin_of(it.get("description") or ""), text, a.dry_run, log, home):
                    notified[key] = "pending"
                    log(f"[carteiro] avisado: {number} PENDÊNCIA")
        if st not in STATES or notified.get(it["id"]) == st:
            continue
        # tarefas de antes do carteiro existir não geram aviso retroativo
        updated = it.get("updatedAt") or ""
        try:
            ts = time.mktime(time.strptime(updated[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            ts = time.time()
        if ts < started_at - 60:
            notified[it["id"]] = st
            continue
        try:
            text = message(it, last_comment(it["id"]))
        except Exception as e:
            log(f"[carteiro] não li a tarefa {it.get('identifier')}: {e.__class__.__name__}")
            continue
        if send(origin_of(it.get("description") or ""), text, a.dry_run, log, home):
            notified[it["id"]] = st
            log(f"[carteiro] avisado: {it.get('identifier')} {STATES[st]}")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
