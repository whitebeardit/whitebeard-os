#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Exporta o estado canônico de uma instalação do Whitebeard OS num bundle .tar.gz (ou só lista, com --dry-run).

Stores (o que importa para "levar tudo"):
  jarvis     $JARVIS_HOME sem tools/, compose/ e render/ (regeneráveis); secrets.env só com --with-secrets
  hermes     $HERMES_HOME sem código, caches, tokens por máquina e sandboxes
  paperclip  ~/.paperclip/instances/<id>: config.json, data/storage/ e o backup de banco mais recente
  knowledge  dump do Postgres (pg_dump via compose) e a home do GBrain, se o compose estiver no ar
  claude     ~/.claude: settings.json, CLAUDE.md, keybindings.json (nunca ~/.claude.json: tem tokens)
  vault      só referência (remoto e commit); com --with-vault, o conteúdo
Derivados (venvs, caches, node_modules, modelos baixados) ficam de fora: reinstalam.
O tenant pode declarar em jarvis.yaml `exportacao.grupos: {financeiro: [globs]}`; `--sem financeiro` exclui o grupo.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERMES_EXCLUDE = [
    "hermes-agent", "hermes-agent/**", "tools", "tools/**", "cache", "cache/**", "sandboxes", "sandboxes/**",
    "tmp", "tmp/**", "logs", "logs/**", "backups", "backups/**", "state-snapshots", "state-snapshots/**",
    "audio_cache", "audio_cache/**", "image_cache", "image_cache/**", "images", "images/**", "pastes", "pastes/**",
    "lsp", "lsp/**", "node_modules", "**/node_modules", "**/node_modules/**", "**/.venv", "**/.venv/**",
    "**/venv", "**/venv/**", "**/__pycache__", "**/__pycache__/**", "*.lock", "*.pid", "gateway.pid",
    "*.log", "*.db-shm", "*.db-wal", "auth.json", "auth.json.*", "mcp-tokens", "mcp-tokens/**",
    "google_token.json", "google_oauth_pending.json", "google_client_secret.json", ".env", ".env.*",
]
JARVIS_EXCLUDE = ["tools", "tools/**", "compose", "compose/**", "render", "render/**", "vault-index", "vault-index/**"]
CLAUDE_INCLUDE = ["settings.json", "CLAUDE.md", "keybindings.json"]


def log(msg: str) -> None:
    print(f"[export] {msg}")


def excluded(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def walk(root: Path, exclude: list[str]) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames if not excluded(os.path.join(rel_dir, d), exclude)]
        for f in filenames:
            rel = os.path.join(rel_dir, f)
            p = Path(dirpath) / f
            if excluded(rel, exclude) or p.is_symlink():
                continue
            out.append(p)
    return out


def size_of(files: list[Path]) -> int:
    return sum(p.stat().st_size for p in files if p.exists())


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="arquivo .tar.gz de saída (padrão: ./whitebeard-export-<tenant>-<data>.tar.gz)")
    ap.add_argument("--jarvis-home", default=os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--paperclip-home", default=os.environ.get("PAPERCLIP_HOME", str(Path.home() / ".paperclip")))
    ap.add_argument("--instance", default="default")
    ap.add_argument("--only", default="", help="stores separados por vírgula (padrão: todos)")
    ap.add_argument("--sem", default="", help="grupos do tenant a excluir (exportacao.grupos), ex.: financeiro")
    ap.add_argument("--with-secrets", action="store_true", help="inclui secrets.env e .env do Hermes (cuidado)")
    ap.add_argument("--with-vault", action="store_true", help="inclui o conteúdo do vault (normalmente é git)")
    ap.add_argument("--dry-run", action="store_true", help="só lista o que seria exportado, com tamanhos")
    a = ap.parse_args()

    jh, hh = Path(a.jarvis_home), Path(a.hermes_home)
    ph = Path(a.paperclip_home) / "instances" / a.instance
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    sem = {s.strip() for s in a.sem.split(",") if s.strip()}

    tenant = "desconhecido"
    tenant_exclude: list[str] = []
    vault_path = None
    ty = jh / "jarvis.yaml"
    if ty.exists():
        data = yaml.safe_load(ty.read_text(encoding="utf-8")) or {}
        tenant = data.get("tenant", tenant)
        grupos = (data.get("exportacao") or {}).get("grupos") or {}
        for g in sem:
            if g not in grupos:
                log(f"aviso: grupo '{g}' não existe em exportacao.grupos do tenant")
            tenant_exclude += list(grupos.get(g, []))
        if (data.get("vault") or {}).get("path"):
            vault_path = Path.home() / data["vault"]["path"]

    stores: dict[str, dict] = {}

    def add_store(name: str, root: Path, files: list[Path], note: str = "") -> None:
        if only and name not in only:
            return
        stores[name] = {"root": str(root), "files": files, "bytes": size_of(files), "note": note}

    jx = JARVIS_EXCLUDE + ([] if a.with_secrets else ["secrets.env"]) + tenant_exclude
    add_store("jarvis", jh, walk(jh, jx))
    hx = [p for p in HERMES_EXCLUDE if a.with_secrets is False or p not in (".env", ".env.*")] + tenant_exclude
    add_store("hermes", hh, walk(hh, hx))
    pfiles: list[Path] = []
    if (ph / "config.json").exists():
        pfiles.append(ph / "config.json")
    pfiles += walk(ph / "data" / "storage", tenant_exclude)
    backups = sorted((ph / "data" / "backups").glob("*")) if (ph / "data" / "backups").is_dir() else []
    backups = [b for b in backups if b.is_file()]
    if backups:
        pfiles.append(max(backups, key=lambda p: p.stat().st_mtime))
    add_store("paperclip", ph, pfiles, "" if backups else "sem backup de banco em data/backups; rode `paperclipai db:backup` antes")
    cfiles = [Path.home() / ".claude" / f for f in CLAUDE_INCLUDE if (Path.home() / ".claude" / f).exists()]
    add_store("claude", Path.home() / ".claude", cfiles)

    # knowledge: dump do banco e home do GBrain, só se o compose estiver no ar
    kdir = jh / "compose" / "knowledge"
    kfiles: list[Path] = []
    ktmp = None
    if (not only or "knowledge" in only) and (kdir / ".env").exists():
        running = subprocess.run(["docker", "compose", "--project-directory", str(kdir), "ps", "-q", "db"],
                                 capture_output=True, text=True).stdout.strip()
        if running:
            if a.dry_run:
                stores["knowledge"] = {"root": str(kdir), "files": [], "bytes": 0, "note": "pg_dump + volume gbrain_home (tamanho só na execução real)"}
            else:
                ktmp = Path(tempfile.mkdtemp(prefix="wb-knowledge-"))
                dump = ktmp / "gbrain.sql.gz"
                with open(dump, "wb") as fh:
                    p1 = subprocess.Popen(["docker", "compose", "--project-directory", str(kdir), "exec", "-T", "db",
                                           "pg_dump", "-U", "gbrain", "gbrain"], stdout=subprocess.PIPE)
                    subprocess.run(["gzip", "-c"], stdin=p1.stdout, stdout=fh, check=True)
                    p1.wait()
                proj = "whitebeard-knowledge"
                subprocess.run(["docker", "run", "--rm", "-v", f"{proj}_gbrain_home:/v:ro", "-v", f"{ktmp}:/out",
                                "alpine", "tar", "czf", "/out/gbrain_home.tgz", "-C", "/v", "."], check=True)
                kfiles = [dump, ktmp / "gbrain_home.tgz"]
                stores["knowledge"] = {"root": str(ktmp), "files": kfiles, "bytes": size_of(kfiles), "note": ""}
        else:
            log("knowledge: compose não está no ar; store pulado (suba o compose ou exporte depois)")

    vault_info = None
    if vault_path and vault_path.is_dir():
        head = subprocess.run(["git", "-C", str(vault_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        remote = subprocess.run(["git", "-C", str(vault_path), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        vault_info = {"path": str(vault_path), "remote": remote, "head": head}
        if a.with_vault and (not only or "vault" in only):
            add_store("vault", vault_path, walk(vault_path, [".git", ".git/**"]))

    total = sum(s["bytes"] for s in stores.values())
    log(f"tenant={tenant} stores={', '.join(stores) or '-'} total={human(total)}" + (" (dry-run)" if a.dry_run else ""))
    for name, s in stores.items():
        log(f"  {name:10s} {len(s['files']):6d} arquivos  {human(s['bytes']):>9s}  {s['root']}" + (f"  [{s['note']}]" if s["note"] else ""))
    if vault_info:
        log(f"  vault      referência: {vault_info['remote']} @ {vault_info['head'][:12]}" + ("" if a.with_vault else "  (conteúdo não incluído; é git)"))
    if a.dry_run:
        return 0

    out = Path(a.out) if a.out else Path.cwd() / f"whitebeard-export-{tenant}-{datetime.now().strftime('%Y%m%d-%H%M')}.tar.gz"
    manifest = {
        "schema": 1, "tenant": tenant, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "with_secrets": a.with_secrets, "excluded_groups": sorted(sem), "vault": vault_info,
        "stores": {n: {"root": s["root"], "files": len(s["files"]), "bytes": s["bytes"]} for n, s in stores.items()},
    }
    with tarfile.open(out, "w:gz") as tar:
        for name, s in stores.items():
            root = Path(s["root"])
            for f in s["files"]:
                tar.add(f, arcname=f"{name}/{f.relative_to(root)}", recursive=False)
        info = tarfile.TarInfo("manifest.json")
        payload = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        info.size = len(payload)
        import io
        tar.addfile(info, io.BytesIO(payload))
    os.chmod(out, 0o600)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / (out.name + ".sha256")).write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    if ktmp:
        for f in kfiles:
            f.unlink(missing_ok=True)
        ktmp.rmdir()
    log(f"bundle: {out} ({human(out.stat().st_size)}) sha256={sha[:16]}…  modo 600")
    if not a.with_secrets:
        log("segredos NÃO incluídos: secrets.env e .env do Hermes ficam de fora por padrão (--with-secrets para incluir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
