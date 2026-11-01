#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# ///
"""Importa um bundle gerado por wb_export.py numa instalação do Whitebeard OS (já com o bootstrap rodado).

Stores e destino:
  jarvis     → $JARVIS_HOME (mescla; nunca sobrescreve secrets.env existente)
  hermes     → $HERMES_HOME (mescla; nunca sobrescreve .env existente)
  paperclip  → ~/.paperclip/instances/<id>: config.json e data/storage/; o backup de banco vai para
               data/backups/ e precisa ser restaurado com o Paperclip parado (instruções no fim)
  knowledge  → gbrain.sql.gz restaurado no Postgres do compose e gbrain_home.tgz no volume (compose precisa estar no ar)
  claude     → ~/.claude (só settings.json, CLAUDE.md, keybindings.json; não sobrescreve existentes)
  vault      → caminho do tenant, se o bundle trouxer conteúdo (senão, clone o remoto registrado no manifesto)
Com --dry-run só lista. Com --only a,b restringe. Arquivos existentes são sobrescritos (exceto segredos), e
uma cópia .antes-import fica ao lado quando o conteúdo difere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

NEVER_OVERWRITE = {"jarvis/secrets.env", "hermes/.env"}
CLAUDE_NO_OVERWRITE = {"claude/settings.json", "claude/CLAUDE.md", "claude/keybindings.json"}


def log(msg: str) -> None:
    print(f"[import] {msg}")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("--jarvis-home", default=os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--paperclip-home", default=os.environ.get("PAPERCLIP_HOME", str(Path.home() / ".paperclip")))
    ap.add_argument("--instance", default="default")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    bundle = Path(a.bundle)
    if not bundle.is_file():
        log(f"bundle não encontrado: {bundle}")
        return 2
    sidecar = bundle.parent / (bundle.name + ".sha256")
    if sidecar.exists():
        expected = sidecar.read_text().split()[0]
        if sha(bundle) != expected:
            log("sha256 do bundle não confere com o .sha256 ao lado; abortando")
            return 2
        log("sha256 confere")
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    dest_root = {
        "jarvis": Path(a.jarvis_home),
        "hermes": Path(a.hermes_home),
        "paperclip": Path(a.paperclip_home) / "instances" / a.instance,
        "claude": Path.home() / ".claude",
    }
    tmp = Path(tempfile.mkdtemp(prefix="wb-import-"))
    with tarfile.open(bundle, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        bad = [m.name for m in members if m.name.startswith("/") or ".." in Path(m.name).parts]
        if bad:
            log(f"bundle com caminhos inválidos: {bad[:3]}; abortando")
            return 2
        tar.extractall(tmp, members=members)
    manifest = json.loads((tmp / "manifest.json").read_text(encoding="utf-8")) if (tmp / "manifest.json").exists() else {}
    log(f"bundle de tenant={manifest.get('tenant', '?')} criado em {manifest.get('created', '?')}; stores: {', '.join(manifest.get('stores', {}))}")
    if manifest.get("vault") and "vault" not in manifest.get("stores", {}):
        v = manifest["vault"]
        dest_root["vault"] = Path(v["path"])
        log(f"vault: conteúdo não veio no bundle; clone {v.get('remote')} em {v.get('path')} (commit {str(v.get('head', ''))[:12]})")
    elif manifest.get("vault"):
        dest_root["vault"] = Path(manifest["vault"]["path"])

    copied = skipped = backed = 0
    knowledge_files: dict[str, Path] = {}
    for m in members:
        rel = Path(m.name)
        if rel.name == "manifest.json":
            continue
        store = rel.parts[0]
        if only and store not in only:
            continue
        if store == "knowledge":
            knowledge_files[rel.name] = tmp / rel
            continue
        if store not in dest_root:
            log(f"store desconhecido no bundle: {store}; ignorado")
            continue
        src = tmp / rel
        dst = dest_root[store] / Path(*rel.parts[1:])
        key = f"{store}/{Path(*rel.parts[1:])}"
        if (key in NEVER_OVERWRITE or key in CLAUDE_NO_OVERWRITE) and dst.exists():
            skipped += 1
            continue
        if a.dry_run:
            copied += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and sha(dst) != sha(src):
            shutil.copy2(dst, dst.with_name(dst.name + ".antes-import"))
            backed += 1
        shutil.copy2(src, dst)
        if key in ("jarvis/secrets.env", "hermes/.env"):
            os.chmod(dst, 0o600)
        copied += 1
    log(f"arquivos: {copied} copiados, {skipped} preservados (segredos/configs existentes), {backed} com cópia .antes-import" + (" (dry-run)" if a.dry_run else ""))

    if knowledge_files and (not only or "knowledge" in only):
        kdir = Path(a.jarvis_home) / "compose" / "knowledge"
        if a.dry_run:
            log(f"knowledge: restauraria {', '.join(knowledge_files)} no compose em {kdir}")
        elif not (kdir / ".env").exists():
            log("knowledge: compose não instalado neste host (rode o bootstrap com --role knowledge); dump preservado em " + str(tmp))
            return 0
        else:
            running = subprocess.run(["docker", "compose", "--project-directory", str(kdir), "ps", "-q", "db"],
                                     capture_output=True, text=True).stdout.strip()
            if not running:
                log("knowledge: suba o compose (db) antes de importar; dump preservado em " + str(tmp))
                return 0
            if "gbrain.sql.gz" in knowledge_files:
                with open(knowledge_files["gbrain.sql.gz"], "rb") as fh:
                    p1 = subprocess.Popen(["gzip", "-dc"], stdin=fh, stdout=subprocess.PIPE)
                    subprocess.run(["docker", "compose", "--project-directory", str(kdir), "exec", "-T", "db",
                                    "psql", "-U", "gbrain", "-d", "gbrain", "-q"], stdin=p1.stdout, check=True)
                    p1.wait()
                log("knowledge: banco restaurado")
            if "gbrain_home.tgz" in knowledge_files:
                subprocess.run(["docker", "run", "--rm", "-v", "whitebeard-knowledge_gbrain_home:/v",
                                "-v", f"{knowledge_files['gbrain_home.tgz'].parent}:/in:ro", "alpine",
                                "tar", "xzf", "/in/gbrain_home.tgz", "-C", "/v"], check=True)
                log("knowledge: home do GBrain restaurada; reinicie o serviço gbrain")

    pb = dest_root["paperclip"] / "data" / "backups"
    if pb.is_dir() and any(pb.iterdir()) and (not only or "paperclip" in only):
        log(f"paperclip: backup de banco em {pb}. Restaurar com o Paperclip parado: `paperclipai service stop` e, conforme a versão, `paperclipai db:restore <arquivo>` ou restauração manual no Postgres embutido; depois `paperclipai service start`.")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
