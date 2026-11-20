#!/usr/bin/env python3
"""i18n_lint.py — toda frase que o kernel passa por T("...") precisa de tradução em inglês (kernel/i18n/en/*.json),
a mesma frase não pode ter duas traduções diferentes e as {variáveis} têm de aparecer nos dois lados.
Uso: uv run kernel/bin/i18n_lint.py   (sai com código 1 se faltar algo)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent
DIR = BIN.parent / "i18n" / "en"


def phrases() -> dict[str, str]:
    found: dict[str, str] = {}
    for f in sorted(BIN.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "T" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.setdefault(a.value, f"{f.name}:{node.lineno}")
    return found


def main() -> int:
    errors: list[str] = []
    en: dict[str, tuple[str, str]] = {}
    for f in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            errors.append(f"{f.name}: JSON inválido ({e})")
            continue
        for pt, english in d.items():
            if pt in en and en[pt][1] != english:
                errors.append(f"duas traduções para {pt!r}: {en[pt][0]} e {f.name}")
            en[pt] = (f.name, english)
            v = lambda s: sorted(re.findall(r"\{(\w+)\}", s))
            if v(pt) != v(english):
                errors.append(f"{f.name}: variáveis diferentes em {pt!r}")
    for phrase, where in phrases().items():
        if phrase not in en:
            errors.append(f"sem tradução ({where}): {phrase!r}")
    for e in errors:
        print(f"[i18n-lint] {e}")
    print(f"[i18n-lint] {'ok' if not errors else 'FALHOU'}: {len(phrases())} frase(s) em T(), {len(en)} tradução(ões), {len(errors)} problema(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
