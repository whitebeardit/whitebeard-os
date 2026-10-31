#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# ///
"""Lint de privacidade do kernel do Whitebeard OS.

Falha (saída 1) se encontrar, nos caminhos indicados, identidade de tenant, caminho absoluto de usuário,
chat id, conta de nuvem, e-mail ou segredo. Um pacote de tenant pode passar a própria lista de termos
proibidos com --denylist (um termo por linha; comparação sem diferenciar maiúsculas). A lista não fica
no kernel. Uma linha pode ser liberada com o marcador `privacy-lint: allow`.

Uso: privacy_lint.py [--denylist ARQUIVO] [CAMINHO ...]
Padrão de caminhos: kernel bootstrap.sh README.md docs tenants .github test
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALLOW = "privacy-lint: allow"
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}
DEFAULT_PATHS = ["kernel", "bootstrap.sh", "README.md", "docs", "tenants", ".github", "test"]

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("caminho-home-absoluto", re.compile(r"(?<![\w/])/(?:home|Users)/[A-Za-z0-9._-]+")),
    ("telegram-chat-id", re.compile(r"(?<![\w.])-100\d{9,}\b|(?<![\w.-])-\d{9,13}\b")),
    ("aws-account-id", re.compile(r"(?<![\d.-])\d{12}(?![\d.-])")),
    ("email", re.compile(r"\b(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}\b")),
    ("ipv4-rede-privada", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("token-telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("token-anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    ("token-openai", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("token-github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("chave-privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def mask(text: str) -> str:
    return text if len(text) <= 6 else text[:4] + "…" + text[-2:]


def is_binary(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:1024]
    except OSError:
        return True


def iter_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        root = Path(p)
        if root.is_file():
            out.append(root)
            continue
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_file() and not (SKIP_DIRS & set(f.parts)):
                out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--denylist", help="arquivo com termos proibidos, um por linha")
    ap.add_argument("paths", nargs="*", default=DEFAULT_PATHS)
    args = ap.parse_args()

    deny: list[str] = []
    if args.denylist:
        for raw in Path(args.denylist).read_text(encoding="utf-8").splitlines():
            term = raw.strip()
            if term and not term.startswith("#"):
                deny.append(term.lower())

    findings: list[str] = []
    files = iter_files(args.paths)
    for f in files:
        if is_binary(f):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ALLOW in line:
                continue
            for rule, rx in RULES:
                m = rx.search(line)
                if m:
                    findings.append(f"{f}:{n}: {rule}: {mask(m.group(0))}")
            low = line.lower()
            for term in deny:
                if term in low:
                    findings.append(f"{f}:{n}: termo-proibido: {mask(term)}")

    if findings:
        print("[privacy-lint] FALHOU:")
        for item in findings:
            print("  " + item)
        print(f"[privacy-lint] {len(findings)} ocorrência(s) em {len(files)} arquivo(s)")
        return 1
    print(f"[privacy-lint] ok: {len(files)} arquivo(s), {len(RULES)} regras, {len(deny)} termo(s) proibido(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
