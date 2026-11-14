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


def frases() -> dict[str, str]:
    achadas: dict[str, str] = {}
    for f in sorted(BIN.glob("*.py")):
        arvore = ast.parse(f.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "T" and no.args:
                a = no.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    achadas.setdefault(a.value, f"{f.name}:{no.lineno}")
    return achadas


def main() -> int:
    erros: list[str] = []
    en: dict[str, tuple[str, str]] = {}
    for f in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            erros.append(f"{f.name}: JSON inválido ({e})")
            continue
        for pt, ingles in d.items():
            if pt in en and en[pt][1] != ingles:
                erros.append(f"duas traduções para {pt!r}: {en[pt][0]} e {f.name}")
            en[pt] = (f.name, ingles)
            v = lambda s: sorted(re.findall(r"\{(\w+)\}", s))
            if v(pt) != v(ingles):
                erros.append(f"{f.name}: variáveis diferentes em {pt!r}")
    for frase, onde in frases().items():
        if frase not in en:
            erros.append(f"sem tradução ({onde}): {frase!r}")
    for e in erros:
        print(f"[i18n-lint] {e}")
    print(f"[i18n-lint] {'ok' if not erros else 'FALHOU'}: {len(frases())} frase(s) em T(), {len(en)} tradução(ões), {len(erros)} problema(s)")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
