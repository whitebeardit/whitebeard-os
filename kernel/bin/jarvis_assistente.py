#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""jarvis_assistente.py — aplica num perfil do Hermes o que o render escreveu para um assistente.

Uso: jarvis_assistente.py <render/hermes/assistentes/<nome>/> <~/.hermes/profiles/<nome>/>
Lê JARVIS_HOME do ambiente (as skills instaladas pelo kernel ficam em $JARVIS_HOME/skills).

Sai com 0 quando mudou algo, 3 quando já estava em dia, 1 em erro. Nunca imprime valor de segredo.
O que o kernel gerencia é sobrescrito (chaves do config.yaml, variáveis do .env, SOUL.md do kernel,
links de skills do kernel); o que a pessoa fez à mão fica (SOUL.md editado, outras chaves do .env,
skills que não são links do kernel). Só stdlib + PyYAML (o bootstrap roda pelo uv, que traz o PyYAML).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: jarvis_assistente.py <render-dir> <profile-dir>", file=sys.stderr)
        return 1
    src, pdir = Path(sys.argv[1]), Path(sys.argv[2])
    jarvis_skills = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis"))) / "skills"
    nome = pdir.name
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
    soul_path, marca = pdir / "SOUL.md", pdir / ".whitebeard-soul"
    atual = soul_path.read_text(encoding="utf-8") if soul_path.exists() else None
    ultimo = marca.read_text(encoding="utf-8") if marca.exists() else None
    if atual is None or ultimo is None or atual == ultimo:
        if atual != soul_src:
            soul_path.write_text(soul_src, encoding="utf-8")
            changed = True
        if ultimo != soul_src:
            marca.write_text(soul_src, encoding="utf-8")
    else:
        print(f"[whitebeard-os] hermes: SOUL.md de {nome} foi editado à mão; mantido")

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
        alvo = jarvis_skills / sk
        if p.is_symlink() and os.readlink(p) == str(alvo):
            continue
        if p.is_symlink():
            p.unlink()
        elif p.exists():
            print(f"[whitebeard-os] hermes: skill {sk} já existe no perfil de {nome} (não é link); mantida")
            continue
        p.symlink_to(alvo)
        changed = True

    return 0 if changed else 3


if __name__ == "__main__":
    sys.exit(main())
