#!/usr/bin/env python3
"""jarvis_i18n.py — idioma das respostas do kernel à interface.

O texto de origem é o português: T("frase com {nome}", nome=x) devolve a frase em inglês dos dicionários em
kernel/i18n/en/*.json quando o idioma é inglês, e a própria frase em português quando é pt-BR. Frase sem tradução
sai em português (kernel/bin/i18n_lint.py acusa a falta).

O idioma vem da variável JARVIS_IDIOMA, que o servidor da interface preenche com o idioma da página (en | pt-BR).
Sem a variável, inglês. Só passa por T() o que vai para a tela da interface (mensagens de erro, avisos, nomes e
descrições mostrados). O que vira arquivo de configuração, instrução de agente ou mensagem enviada a um chat não
passa: isso segue o idioma do tenant.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "i18n" / "en"


def idioma() -> str:
    return "pt-BR" if os.environ.get("JARVIS_IDIOMA", "").strip().lower().startswith("pt") else "en"


_EN: dict[str, str] | None = None


def dicionario() -> dict[str, str]:
    global _EN
    if _EN is None:
        _EN = {}
        for f in sorted(DIR.glob("*.json")):
            try:
                _EN.update(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass  # dicionário ilegível não derruba a resposta: a frase sai em português
    return _EN


def T(pt: str, **variaveis) -> str:
    """A frase no idioma da interface, com {variáveis} no lugar."""
    s = pt if idioma() == "pt-BR" else dicionario().get(pt, pt)
    if variaveis:
        s = re.sub(r"\{(\w+)\}", lambda m: str(variaveis[m.group(1)]) if m.group(1) in variaveis else m.group(0), s)
    return s
