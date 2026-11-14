#!/usr/bin/env python3
"""jarvis_pensa.py — Quem pensa: qual assinatura (Claude, ChatGPT ou OpenRouter) e qual nível (Rápido, Equilibrado, Mais capaz)
o JARVIS, os assistentes e os cargos usam. Usado pelo jarvis_setup.py (status e comandos da página).

A escolha da página fica em $JARVIS_HOME/pensa.json {provedor, nivel, logados} e vale depois de "colocar em uso"
(o render resolve nível → modelo por kernel/modelos.yaml). O login do ChatGPT é o do Hermes por código de
dispositivo (`hermes auth add openai-codex --type oauth --no-browser`): o processo mostra um endereço e um código, a
pessoa entra na conta dela em qualquer aparelho e o Hermes guarda a credencial no pool dele. Este módulo só lança o
processo em segundo plano, lê o endereço e o código da saída e acompanha. O OpenRouter entra por chave de API
(`hermes auth add openrouter --type api-key`, com a chave pela entrada padrão, nunca na linha de comando): a chave é
conferida no OpenRouter e guardada no pool do Hermes, não em arquivo nosso. Nunca lê nem imprime credencial.
Só biblioteca padrão (a tabela de modelos é lida por regex simples, sem PyYAML).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from jarvis_i18n import T

# entrada: como a pessoa conecta. claude-code = o login do Claude Code; codigo = código de dispositivo; chave = chave de API
PROVEDORES = {"claude": {"hermes": "anthropic", "nome": "Claude", "entrada": "claude-code"},
              "chatgpt": {"hermes": "openai-codex", "nome": "ChatGPT", "entrada": "codigo"},
              "openrouter": {"hermes": "openrouter", "nome": "OpenRouter", "entrada": "chave"}}
NIVEIS = {"rapido": "Rápido", "equilibrado": "Equilibrado", "capaz": "Mais capaz"}
# erros do login gravados em pensa.login.json: ficam em português no arquivo e saem no idioma da interface (login_na_tela)
ERRO_LOGIN_EXPIROU = "O código expirou ou o login foi cancelado. Tente de novo."
ERRO_LOGIN_INCOMPLETO = "o login não terminou"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def nomes_niveis() -> dict:
    """Os nomes dos níveis como a página os mostra (no idioma da interface)."""
    return {"rapido": T("Rápido"), "equilibrado": T("Equilibrado"), "capaz": T("Mais capaz")}


def login_na_tela(estado: dict) -> dict:
    """O estado do login como vai para a página: o erro gravado em português sai no idioma da interface."""
    if estado.get("erro") == ERRO_LOGIN_EXPIROU:
        return {**estado, "erro": T("O código expirou ou o login foi cancelado. Tente de novo.")}
    if estado.get("erro") == ERRO_LOGIN_INCOMPLETO:
        return {**estado, "erro": T("o login não terminou")}
    return estado


def hermes_bin() -> str:
    return shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")


def ler(path: Path, padrao):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def gravar(path: Path, dados) -> None:
    path.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def loja(home: Path) -> dict:
    d = ler(home / "pensa.json", {})
    return d if isinstance(d, dict) else {}


CACHE_S = 60
_HOME: Path | None = None


def logado(provedor: str, fresco: bool = False) -> bool:
    """Com cache de um minuto em $JARVIS_HOME/pensa.cache.json (a página consulta o status a cada poucos segundos)."""
    cache_p = (_HOME / "pensa.cache.json") if _HOME else None
    if cache_p and not fresco:
        c = ler(cache_p, {})
        v = c.get(provedor)
        if isinstance(v, dict) and time.time() - v.get("em", 0) < CACHE_S:
            return bool(v.get("logado"))
    r = _logado(provedor)
    if cache_p:
        c = ler(cache_p, {})
        c[provedor] = {"logado": r, "em": time.time()}
        try:
            gravar(cache_p, c)
        except OSError:
            pass
    return r


ROTULO = "whitebeard-os"  # o rótulo das credenciais que este módulo põe no pool do Hermes


def _chaves(provedor: str) -> list[str]:
    """Rótulos das chaves de API deste provedor no pool do Hermes (`hermes auth list`; o `auth status` não as vê)."""
    try:
        r = subprocess.run([hermes_bin(), "auth", "list", PROVEDORES[provedor]["hermes"]], capture_output=True, text=True, timeout=20,
                           check=False, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
    except (OSError, subprocess.SubprocessError):
        return []
    # linhas como "  #1  whitebeard-os        api_key id=6f4782 priority=0 manual"
    return [m.group(1) for m in re.finditer(r"^\s*#\d+\s+(\S+)\s+api_key\b", ANSI.sub("", r.stdout or ""), re.M)]


def _tirar_chaves(provedor: str) -> None:
    """Tira do pool só as chaves que este módulo pôs (rótulo whitebeard-os); as que a pessoa pôs à mão ficam."""
    for _ in range(10):
        if ROTULO not in _chaves(provedor):
            return
        subprocess.run([hermes_bin(), "auth", "remove", PROVEDORES[provedor]["hermes"], ROTULO], capture_output=True, text=True, timeout=30, check=False)


def _logado(provedor: str) -> bool:
    """O Hermes tem credencial para este provedor? (`hermes auth status`, sem ler o arquivo de credenciais)."""
    if PROVEDORES[provedor]["entrada"] == "chave":
        return bool(_chaves(provedor))
    try:
        r = subprocess.run([hermes_bin(), "auth", "status", PROVEDORES[provedor]["hermes"]], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    # a primeira linha é "<provedor>: logged in" ou "<provedor>: logged out (…)"
    primeira = ((r.stdout or r.stderr).strip().splitlines() or [""])[0].lower()
    return ": logged in" in primeira


def modelos(kernel: Path) -> dict:
    """{provedor: {nivel: modelo}} lido de kernel/modelos.yaml (regex; o arquivo tem formato fixo)."""
    out: dict = {}
    try:
        txt = (kernel / "modelos.yaml").read_text(encoding="utf-8")
    except OSError:
        return out
    atual = None
    for line in txt.splitlines():
        m = re.match(r"^  (%s):\s*$" % "|".join(PROVEDORES), line)
        if m:
            atual = m.group(1)
            out[atual] = {}
            continue
        m = re.match(r"^      (rapido|equilibrado|capaz):\s*([^\s#]+)", line)
        if m and atual:
            out[atual][m.group(1)] = m.group(2)
        if re.match(r"^\S", line):
            atual = None if not line.startswith("provedores") else atual
    return out


def status(home: Path, kernel: Path, tenant_hermes: dict | None = None) -> dict:
    global _HOME
    _HOME = home
    lj = loja(home)
    th = tenant_hermes or {}
    provedor = lj.get("provedor") or th.get("provedor") or ("chatgpt" if th.get("provider") == "openai-codex" else "claude")
    nivel = lj.get("nivel") or th.get("nivel") or "equilibrado"
    logados = {p: logado(p) for p in PROVEDORES}
    login = login_na_tela(ler(home / "pensa.login.json", {}))
    if login.get("estado") in ("esperando", "preparando") and not vivo(login.get("pid")):
        login = atualizar_login(home)
    tab = modelos(kernel)
    aviso = ""
    if not logados.get(provedor) and provedor != "claude":
        aviso = T("O {nome} está escolhido, mas a conta não está conectada: entre de novo ou use o Claude.", nome=PROVEDORES[provedor]["nome"])
        provedor = "claude"
    reserva = next((p for p in PROVEDORES if p != provedor and logados.get(p)), None)
    return {"provedor": provedor, "nivel": nivel, "escolhido": bool(lj) or bool(th.get("provedor") or th.get("nivel")),
            "logados": logados, "reserva": reserva, "aviso": aviso,
            "modelo": (tab.get(provedor) or {}).get(nivel, ""),
            # modelo de cada nível na assinatura em uso: a página mostra no seletor de nível (assistente, cargo)
            "modelos": dict(tab.get(provedor) or {}),
            "modelos_por_provedor": {k: dict(v) for k, v in tab.items()},
            "niveis": [{"id": k, "nome": v} for k, v in nomes_niveis().items()],
            "provedores": [{"id": k, "nome": v["nome"], "entrada": v["entrada"]} for k, v in PROVEDORES.items()],
            "login": {k: login.get(k) for k in ("provedor", "estado", "endereco", "codigo", "erro", "inicio") if login.get(k) is not None} or None}


def definir(home: Path, provedor: str | None, nivel: str | None) -> dict:
    global _HOME
    _HOME = home
    lj = loja(home)
    if provedor is not None:
        if provedor not in PROVEDORES:
            return {"ok": False, "erro": T("provedor desconhecido")}
        if not logado(provedor, fresco=True):
            return {"ok": False, "erro": T("entre na sua conta do {nome} primeiro", nome=PROVEDORES[provedor]["nome"])}
        lj["provedor"] = provedor
    if nivel is not None:
        if nivel not in NIVEIS:
            return {"ok": False, "erro": T("nível desconhecido")}
        lj["nivel"] = nivel
    lj.setdefault("provedor", "claude")
    lj.setdefault("nivel", "equilibrado")
    lj["logados"] = [p for p in PROVEDORES if logado(p)]
    gravar(home / "pensa.json", lj)
    return {"ok": True, "provedor": lj["provedor"], "nivel": lj["nivel"], "logados": lj["logados"]}


def vivo(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def entrar(home: Path, provedor: str) -> dict:
    """Começa o login por código de dispositivo em segundo plano e devolve logo; a página acompanha pelo status."""
    global _HOME
    _HOME = home
    if provedor != "chatgpt":
        return {"ok": False, "erro": T("o Claude entra pelo login do Claude Code, feito na instalação")}
    atual = ler(home / "pensa.login.json", {})
    if atual.get("estado") in ("preparando", "esperando") and vivo(atual.get("pid")):
        return {"ok": True, "login": atual}
    logf = home / "logs" / "pensa-login.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    with open(logf, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([hermes_bin(), "auth", "add", "openai-codex", "--type", "oauth", "--no-browser", "--label", "whitebeard-os"],
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PYTHONUNBUFFERED": "1"})
    estado = {"provedor": provedor, "estado": "preparando", "pid": proc.pid, "inicio": int(time.time())}
    gravar(home / "pensa.login.json", estado)
    for _ in range(30):  # até ~15 s pelo endereço e o código
        time.sleep(0.5)
        estado = atualizar_login(home)
        if estado.get("estado") != "preparando":
            break
    return {"ok": estado.get("estado") != "erro", "login": {k: v for k, v in estado.items() if k != "pid"}, **({"erro": estado.get("erro")} if estado.get("erro") else {})}


def atualizar_login(home: Path) -> dict:
    """Lê a saída do processo: endereço e código; ao terminar, confere no Hermes e registra o provedor em logados."""
    global _HOME
    _HOME = home
    estado = ler(home / "pensa.login.json", {})
    if not estado:
        return {}
    try:
        txt = ANSI.sub("", (home / "logs" / "pensa-login.log").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        txt = ""
    m_url = re.search(r"(https://\S+/codex/device\S*)", txt)
    m_cod = re.search(r"Enter this code:\s*\n\s*([A-Z0-9][A-Z0-9-]{3,})", txt)
    if m_url:
        estado["endereco"] = m_url.group(1)
    if m_cod:
        estado["codigo"] = m_cod.group(1)
    if estado.get("estado") == "preparando" and m_url and m_cod:
        estado["estado"] = "esperando"
    if not vivo(estado.get("pid")) and estado.get("estado") in ("preparando", "esperando"):
        if logado(estado.get("provedor", "chatgpt"), fresco=True):
            estado["estado"] = "conectado"
            lj = loja(home)
            lj["logados"] = sorted(set(lj.get("logados") or []) | {estado.get("provedor", "chatgpt")})
            lj.setdefault("provedor", "claude")
            lj.setdefault("nivel", "equilibrado")
            gravar(home / "pensa.json", lj)
        else:
            ult = [l.strip() for l in txt.splitlines() if l.strip()][-1:] or [ERRO_LOGIN_INCOMPLETO]
            estado["estado"] = "erro"
            estado["erro"] = (ERRO_LOGIN_EXPIROU if "cancel" in txt.lower() or "expired" in txt.lower()
                              else ult[0][:200])
    gravar(home / "pensa.login.json", estado)
    return login_na_tela(estado)


def cancelar(home: Path) -> dict:
    global _HOME
    _HOME = home
    estado = ler(home / "pensa.login.json", {})
    if vivo(estado.get("pid")):
        try:
            os.killpg(int(estado["pid"]), signal.SIGTERM)
        except OSError:
            pass
    estado["estado"] = "cancelado"
    gravar(home / "pensa.login.json", estado)
    return {"ok": True}


def chave_valida(chave: str) -> tuple[bool, str]:
    """Confere a chave no OpenRouter (GET /api/v1/key). (ok, motivo). A chave não entra em mensagem nem em log."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {chave}", "User-Agent": "whitebeard-os"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return (r.status == 200, "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, T("o OpenRouter recusou a chave; confira em openrouter.ai/keys e cole de novo")
        return False, T("o OpenRouter respondeu {codigo}; tente de novo", codigo=e.code)
    except (OSError, ValueError):
        return False, T("não consegui falar com o OpenRouter; confira a internet e tente de novo")


def conectar_chave(home: Path, provedor: str, chave: str) -> dict:
    """Conecta um provedor que entra por chave de API (OpenRouter): confere a chave e a guarda no pool do Hermes."""
    global _HOME
    _HOME = home
    if PROVEDORES.get(provedor, {}).get("entrada") != "chave":
        return {"ok": False, "erro": T("este provedor não entra por chave")}
    chave = (chave or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,300}", chave):
        return {"ok": False, "erro": T("cole a chave do OpenRouter (começa com sk-or-)")}
    ok, motivo = chave_valida(chave)
    if not ok:
        return {"ok": False, "erro": motivo}
    hp = PROVEDORES[provedor]["hermes"]
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        # chave nova substitui a anterior; sem terminal, o Hermes lê a chave da entrada padrão
        _tirar_chaves(provedor)
        r = subprocess.run([hermes_bin(), "auth", "add", hp, "--type", "api-key", "--label", ROTULO, "--priority", "0"], input=chave + "\n",
                           capture_output=True, text=True, timeout=40, check=False, env=env, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "erro": T("não consegui guardar a chave no Hermes: {classe}", classe=e.__class__.__name__)}
    if r.returncode != 0 or not logado(provedor, fresco=True):
        return {"ok": False, "erro": T("o Hermes não aceitou a chave; atualize o Whitebeard OS e tente de novo")}
    lj = loja(home)
    lj["logados"] = sorted(set(lj.get("logados") or []) | {provedor})
    lj.setdefault("provedor", "claude")
    lj.setdefault("nivel", "equilibrado")
    gravar(home / "pensa.json", lj)
    return {"ok": True, "provedor": lj["provedor"], "logados": lj["logados"]}


def sair(home: Path, provedor: str) -> dict:
    """Tira a assinatura do pool do Hermes (ChatGPT ou OpenRouter; a do Claude é a do Claude Code). Se era a
    principal, o Claude volta a ser."""
    global _HOME
    _HOME = home
    if PROVEDORES.get(provedor, {}).get("entrada") not in ("codigo", "chave"):
        return {"ok": False, "erro": T("a conta do Claude é a do Claude Code; saia por lá")}
    try:
        if PROVEDORES[provedor]["entrada"] == "chave":
            _tirar_chaves(provedor)
        else:
            subprocess.run([hermes_bin(), "auth", "logout", PROVEDORES[provedor]["hermes"]], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "erro": T("não consegui sair: {classe}", classe=e.__class__.__name__)}
    lj = loja(home)
    lj["logados"] = [p for p in (lj.get("logados") or []) if p != provedor]
    logado(provedor, fresco=True)
    if lj.get("provedor") == provedor:
        lj["provedor"] = "claude"
    gravar(home / "pensa.json", lj)
    return {"ok": True, "provedor": lj.get("provedor", "claude")}
