#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""jarvis_hermes_plugins.py — instala num home do Hermes os plugins que os add-ons declaram.

Uso: jarvis_hermes_plugins.py <render/hermes/plugins.json> <home do Hermes ou pasta de um perfil>

O render escreve em plugins.json a lista dos plugins dos add-ons ligados (`[{"name", "path"}]`). Aqui cada
pasta é copiada para <home>/plugins/<nome>/ e o nome entra em `plugins.enabled` do config.yaml (o Hermes só
carrega plugin de usuário que está nessa lista). O que já estava em `plugins.enabled` fica. Plugin que saiu
(add-on desligado) perde a pasta e o nome na lista; só os que o kernel instalou (lembrados em
.whitebeard-plugins) são tocados, nunca um plugin que a pessoa pôs à mão.

Sai com 0 quando mudou algo (o gateway precisa reiniciar para carregar), 3 quando já estava em dia, 1 em erro.
Só stdlib + PyYAML.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

MANAGED = ".whitebeard-plugins"   # no home: os nomes que o kernel instalou
MARK = ".whitebeard-addon"        # dentro da pasta do plugin: a impressão do conteúdo copiado
SKIP = ("__pycache__",)


def tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.name != MARK and not any(s in p.parts for s in SKIP) and p.suffix != ".pyc":
            h.update(str(p.relative_to(folder)).encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def apply_plugins(wanted: list[dict], home: Path) -> bool:
    """Deixa <home>/plugins e plugins.enabled de acordo com `wanted`. Devolve True se mudou algo."""
    changed = False
    managed_path = home / MANAGED
    managed = managed_path.read_text(encoding="utf-8").split() if managed_path.is_file() else []
    names = [p["name"] for p in wanted]
    if not names and not managed:
        return False
    plugins_dir = home / "plugins"
    for stale in [n for n in managed if n not in names]:
        d = plugins_dir / stale
        if (d / MARK).is_file():  # só apaga o que o kernel copiou
            shutil.rmtree(d)
            changed = True
    for p in wanted:
        src, dest = Path(p["path"]), plugins_dir / p["name"]
        digest = tree_hash(src)
        mark = dest / MARK
        if mark.is_file() and mark.read_text(encoding="utf-8").strip() == digest:
            continue
        if dest.exists() and not mark.is_file():
            raise RuntimeError(f"já existe um plugin `{p['name']}` em {dest} que o kernel não instalou; "
                               "tire-o de lá ou dê outro nome ao plugin do add-on")
        tmp = plugins_dir / f".{p['name']}.new"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))
        (tmp / MARK).write_text(digest + "\n", encoding="utf-8")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
        changed = True

    cfg_path = home / "config.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}) or {}
    section = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    before = list(section.get("enabled") or []) if isinstance(section.get("enabled"), list) else []
    after = [n for n in before if n not in managed or n in names]
    after += [n for n in names if n not in after]
    if after != before:
        cfg["plugins"] = {**section, "enabled": after}
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(cfg_path, 0o600)
        changed = True
    if names:
        if managed != names:
            managed_path.write_text("\n".join(names) + "\n", encoding="utf-8")
    elif managed_path.exists():
        managed_path.unlink()
    return changed


def load_wanted(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [p for p in data if isinstance(p, dict) and p.get("name") and p.get("path")]


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: jarvis_hermes_plugins.py <plugins.json> <hermes-home>", file=sys.stderr)
        return 1
    home = Path(sys.argv[2])
    if not home.is_dir():
        return 3
    try:
        changed = apply_plugins(load_wanted(Path(sys.argv[1])), home)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as e:
        print(f"[whitebeard-os] aviso: plugins do Hermes não aplicados em {home}: {e}")
        return 1
    return 0 if changed else 3


if __name__ == "__main__":
    sys.exit(main())
