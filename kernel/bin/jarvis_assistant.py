#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""jarvis_assistant.py — aplica num perfil do Hermes o que o render escreveu para um assistente.

Uso: jarvis_assistant.py <render/hermes/assistants/<nome>/> <~/.hermes/profiles/<nome>/>
Lê JARVIS_HOME do ambiente (as skills instaladas pelo kernel ficam em $JARVIS_HOME/skills).

Sai com 0 quando mudou algo, 3 quando já estava em dia, 1 em erro. Nunca imprime valor de segredo.

Quando mudam as skills do assistente (a lista ou o conteúdo), os MCPs ou o SOUL, as conversas abertas nos
canais são encerradas (como um /reset em cada canal): o gateway, ao reiniciar, descarta essas rotas e a próxima
mensagem abre uma conversa nova, que já carrega o perfil novo. Sessões que não são de canal (terminal, Paperclip)
ficam. Na primeira aplicação, nada é encerrado.
Os plugins do Hermes que os add-ons declaram (render/hermes/plugins.json) são copiados para o perfil e ligados em
`plugins.enabled`; mudança neles conta como mudança (o gateway reinicia), sem encerrar conversas.
O que o kernel gerencia é sobrescrito (chaves do config.yaml, variáveis do .env, SOUL.md do kernel,
links de skills do kernel); o que a pessoa fez à mão fica (SOUL.md editado, outras chaves do .env,
skills que não são links do kernel). Só stdlib + PyYAML (o bootstrap roda pelo uv, que traz o PyYAML).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_hermes_plugins import apply_plugins, load_wanted  # noqa: E402


CHANNEL_SOURCES = ("discord", "telegram", "whatsapp", "slack", "signal")
FINGERPRINT = ".whitebeard-fingerprint"


def fingerprint(mcp: object, skills: list[str], jarvis_skills: Path, soul: str) -> str:
    """O que a conversa carrega no começo: MCPs, skills (nome e conteúdo do SKILL.md) e SOUL."""
    h = hashlib.sha256(json.dumps(mcp, sort_keys=True, default=str).encode("utf-8"))
    for sk in sorted(skills):
        f = jarvis_skills / sk / "SKILL.md"
        h.update(f"\0{sk}\0".encode("utf-8"))
        h.update(f.read_bytes() if f.is_file() else b"")
    h.update(b"\0soul\0" + soul.encode("utf-8"))
    return h.hexdigest()


def end_channel_conversations(db_path: Path) -> int:
    """Encerra (end_reason=session_reset) as sessões abertas dos canais no state.db do perfil."""
    if not db_path.is_file():
        return 0
    try:
        con = sqlite3.connect(db_path, timeout=10)
        try:
            cols = {r[1] for r in con.execute("pragma table_info(sessions)")}
            if not {"source", "ended_at", "end_reason"} <= cols:
                return 0
            marks = ",".join("?" * len(CHANNEL_SOURCES))
            with con:
                cur = con.execute(f"update sessions set ended_at = ?, end_reason = 'session_reset' "
                                  f"where ended_at is null and end_reason is null and source in ({marks})",
                                  (time.time(), *CHANNEL_SOURCES))
            return cur.rowcount
        finally:
            con.close()
    except sqlite3.Error as e:
        print(f"[whitebeard-os] aviso: não consegui encerrar as conversas abertas ({e}); dê /reset nos canais")
        return 0


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: jarvis_assistant.py <render-dir> <profile-dir>", file=sys.stderr)
        return 1
    src, pdir = Path(sys.argv[1]), Path(sys.argv[2])
    jarvis_home = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    jarvis_skills = jarvis_home / "skills"
    name = pdir.name
    changed = False

    # ---- config.yaml: as chaves do kernel por cima; o resto do que o Hermes escreveu fica
    wanted = yaml.safe_load((src / "config.yaml").read_text(encoding="utf-8")) or {}
    cfg_path = pdir / "config.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}) or {}
    for k, v in wanted.items():
        if cfg.get(k) != v:
            cfg[k] = v
            changed = True
    if "mcp_servers" not in wanted and cfg.get("mcp_servers"):
        del cfg["mcp_servers"]  # tipo sem MCP: nenhum servidor, nem os que estavam
        changed = True
    if changed:
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(cfg_path, 0o600)

    # ---- .env: chaves do kernel atualizadas; as demais ficam
    env_path = pdir / ".env"
    env = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    before = env
    for line in (src / "env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        k = line.split("=", 1)[0]
        if re.search(r"(?m)^" + re.escape(k) + r"=", env):
            if not re.search(r"(?m)^" + re.escape(line) + r"$", env):
                env = re.sub(r"(?m)^" + re.escape(k) + r"=.*$", lambda _: line, env, count=1)
        else:
            env = env.rstrip("\n") + ("\n" if env else "") + line + "\n"
    if env != before:
        env_path.write_text(env, encoding="utf-8")
        changed = True
    os.chmod(env_path, 0o600)

    # ---- SOUL.md: do kernel, a menos que a pessoa tenha editado (a última versão do kernel fica guardada)
    soul_src = (src / "SOUL.md").read_text(encoding="utf-8")
    soul_path, marker = pdir / "SOUL.md", pdir / ".whitebeard-soul"
    current = soul_path.read_text(encoding="utf-8") if soul_path.exists() else None
    last = marker.read_text(encoding="utf-8") if marker.exists() else None
    if current is None or last is None or current == last:
        if current != soul_src:
            soul_path.write_text(soul_src, encoding="utf-8")
            changed = True
        if last != soul_src:
            marker.write_text(soul_src, encoding="utf-8")
    else:
        print(f"[whitebeard-os] hermes: SOUL.md de {name} foi editado à mão; mantido")

    # ---- skills: só as do assistente, como links para as instaladas pelo kernel
    skills_dir = pdir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    wanted_skills = [l.strip() for l in (src / "skills.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    for entry in list(skills_dir.iterdir()):
        if entry.is_symlink() and entry.name not in wanted_skills and str(entry.resolve()).startswith(str(jarvis_skills.resolve())):
            entry.unlink()
            changed = True
    for sk in wanted_skills:
        p = skills_dir / sk
        target = jarvis_skills / sk
        if p.is_symlink() and os.readlink(p) == str(target):
            continue
        if p.is_symlink():
            p.unlink()
        elif p.exists():
            print(f"[whitebeard-os] hermes: skill {sk} já existe no perfil de {name} (não é link); mantida")
            continue
        p.symlink_to(target)
        changed = True

    # ---- plugins do Hermes que os add-ons trazem: copiados para o perfil e ligados em plugins.enabled
    try:
        if apply_plugins(load_wanted(jarvis_home / "render" / "hermes" / "plugins.json"), pdir):
            print(f"[whitebeard-os] hermes: {name}: plugins dos add-ons atualizados")
            changed = True
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as e:
        print(f"[whitebeard-os] aviso: plugins do Hermes não aplicados em {name}: {e}")

    # ---- conversas abertas: encerradas quando muda o que elas carregam no começo
    fp = fingerprint(wanted.get("mcp_servers"), wanted_skills, jarvis_skills, soul_src)
    fp_path = pdir / FINGERPRINT
    old_fp = fp_path.read_text(encoding="utf-8").strip() if fp_path.exists() else None
    if old_fp != fp:
        fp_path.write_text(fp + "\n", encoding="utf-8")
        if old_fp is not None:
            n = end_channel_conversations(pdir / "state.db")
            print(f"[whitebeard-os] hermes: {name}: skills, MCPs ou SOUL mudaram; {n} conversa(s) aberta(s) "
                  "encerrada(s) (a próxima mensagem em cada canal abre uma nova)")
            changed = True

    return 0 if changed else 3


if __name__ == "__main__":
    sys.exit(main())
