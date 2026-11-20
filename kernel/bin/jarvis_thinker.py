#!/usr/bin/env python3
"""jarvis_thinker.py — Quem pensa: qual assinatura (Claude, ChatGPT ou OpenRouter) e qual nível (Rápido, Equilibrado, Mais capaz)
o JARVIS, os assistentes e os cargos usam. Usado pelo jarvis_setup.py (status e comandos da página).

A escolha da página fica em $JARVIS_HOME/thinker.json {provider, level, logged_in} e vale depois de "colocar em uso"
(o render resolve nível → modelo por kernel/models.yaml). O login do ChatGPT é o do Hermes por código de
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jarvis_i18n import T

# entrada: como a pessoa conecta. claude-code = o login do Claude Code; codigo = código de dispositivo; chave = chave de API
PROVIDERS = {"claude": {"hermes": "anthropic", "name": "Claude", "sign_in": "claude-code"},
              "chatgpt": {"hermes": "openai-codex", "name": "ChatGPT", "sign_in": "code"},
              "openrouter": {"hermes": "openrouter", "name": "OpenRouter", "sign_in": "key"}}
LEVELS = {"fast": "Rápido", "balanced": "Equilibrado", "capable": "Mais capaz"}
# erros do login gravados em thinker.login.json: ficam em português no arquivo e saem no idioma da interface (login_na_tela)
LOGIN_EXPIRED_ERROR = "O código expirou ou o login foi cancelado. Tente de novo."
LOGIN_INCOMPLETE_ERROR = "o login não terminou"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def level_names() -> dict:
    """Os nomes dos níveis como a página os mostra (no idioma da interface)."""
    return {"fast": T("Rápido"), "balanced": T("Equilibrado"), "capable": T("Mais capaz")}


def login_for_display(state: dict) -> dict:
    """O estado do login como vai para a página: o erro gravado em português sai no idioma da interface."""
    if state.get("error") == LOGIN_EXPIRED_ERROR:
        return {**state, "error": T("O código expirou ou o login foi cancelado. Tente de novo.")}
    if state.get("error") == LOGIN_INCOMPLETE_ERROR:
        return {**state, "error": T("o login não terminou")}
    return state


def hermes_bin() -> str:
    return shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def store(home: Path) -> dict:
    d = load(home / "thinker.json", {})
    return d if isinstance(d, dict) else {}


CACHE_S = 60
# limite de cada consulta ao Hermes no status: a página espera o status; um Hermes lento (primeira vez, máquina
# carregada) não pode segurar a resposta. Os provedores são consultados em paralelo.
CHECK_TIMEOUT_S = 8
_HOME: Path | None = None


def is_logged_in(provider: str, fresh: bool = False) -> bool:
    """Com cache de um minuto em $JARVIS_HOME/thinker.cache.json (a página consulta o status a cada poucos segundos).
    Se o Hermes não responder a tempo, vale a última resposta conhecida (ou "desconectado"), e nada é gravado."""
    cache_p = (_HOME / "thinker.cache.json") if _HOME else None
    cached = load(cache_p, {}).get(provider) if cache_p else None
    if not isinstance(cached, dict):
        cached = None
    if cached and not fresh and time.time() - cached.get("at", 0) < CACHE_S:
        return bool(cached.get("logged_in"))
    # depois de um login (fresh) a resposta precisa ser certa: espera mais; no status, o limite curto
    r = _check_logged_in(provider, timeout=20 if fresh else CHECK_TIMEOUT_S)
    if r is None:
        return bool(cached.get("logged_in")) if cached else False
    if cache_p:
        c = load(cache_p, {})
        c[provider] = {"logged_in": r, "at": time.time()}
        try:
            save(cache_p, c)
        except OSError:
            pass
    return r


LABEL = "whitebeard-os"  # o rótulo das credenciais que este módulo põe no pool do Hermes


def _keys(provider: str, timeout: float = 20) -> list[str] | None:
    """Rótulos das chaves de API deste provedor no pool do Hermes (`hermes auth list`; o `auth status` não as vê).
    None quando o Hermes não respondeu a tempo."""
    try:
        r = subprocess.run([hermes_bin(), "auth", "list", PROVIDERS[provider]["hermes"]], capture_output=True, text=True,
                           timeout=timeout, check=False, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return []
    # linhas como "  #1  whitebeard-os        api_key id=6f4782 priority=0 manual"
    return [m.group(1) for m in re.finditer(r"^\s*#\d+\s+(\S+)\s+api_key\b", ANSI.sub("", r.stdout or ""), re.M)]


def _remove_keys(provider: str) -> None:
    """Tira do pool só as chaves que este módulo pôs (rótulo whitebeard-os); as que a pessoa pôs à mão ficam."""
    for _ in range(10):
        if LABEL not in (_keys(provider) or []):
            return
        subprocess.run([hermes_bin(), "auth", "remove", PROVIDERS[provider]["hermes"], LABEL], capture_output=True, text=True, timeout=30, check=False)


def _check_logged_in(provider: str, timeout: float = CHECK_TIMEOUT_S) -> bool | None:
    """O Hermes tem credencial para este provedor? (`hermes auth status`, sem ler o arquivo de credenciais).
    None quando o Hermes não respondeu a tempo."""
    if PROVIDERS[provider]["sign_in"] == "key":
        keys = _keys(provider, timeout=timeout)
        return None if keys is None else bool(keys)
    try:
        r = subprocess.run([hermes_bin(), "auth", "status", PROVIDERS[provider]["hermes"]], capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return False
    # a primeira linha é "<provedor>: logged in" ou "<provedor>: logged out (…)"
    first_line = ((r.stdout or r.stderr).strip().splitlines() or [""])[0].lower()
    return ": logged in" in first_line


def models(kernel: Path) -> dict:
    """{provedor: {nivel: modelo}} lido de kernel/models.yaml (regex; o arquivo tem formato fixo)."""
    out: dict = {}
    try:
        txt = (kernel / "models.yaml").read_text(encoding="utf-8")
    except OSError:
        return out
    current = None
    for line in txt.splitlines():
        m = re.match(r"^  (%s):\s*$" % "|".join(PROVIDERS), line)
        if m:
            current = m.group(1)
            out[current] = {}
            continue
        m = re.match(r"^      (fast|balanced|capable):\s*([^\s#]+)", line)
        if m and current:
            out[current][m.group(1)] = m.group(2)
        if re.match(r"^\S", line):
            current = None if not line.startswith("providers") else current
    return out


def status(home: Path, kernel: Path, tenant_hermes: dict | None = None) -> dict:
    global _HOME
    _HOME = home
    saved = store(home)
    th = tenant_hermes or {}
    provider = saved.get("provider") or th.get("subscription") or ("chatgpt" if th.get("provider") == "openai-codex" else "claude")
    level = saved.get("level") or th.get("level") or "balanced"
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:  # em paralelo: o mais lento manda, não a soma
        logged_in = dict(zip(PROVIDERS, pool.map(is_logged_in, PROVIDERS)))
    login = login_for_display(load(home / "thinker.login.json", {}))
    if login.get("state") in ("waiting", "preparing") and not alive(login.get("pid")):
        login = refresh_login(home)
    tab = models(kernel)
    warning = ""
    if not logged_in.get(provider) and provider != "claude":
        warning = T("O {name} está escolhido, mas a conta não está conectada: entre de novo ou use o Claude.", name=PROVIDERS[provider]["name"])
        provider = "claude"
    fallback = next((p for p in PROVIDERS if p != provider and logged_in.get(p)), None)
    return {"provider": provider, "level": level, "chosen": bool(saved) or bool(th.get("subscription") or th.get("level")),
            "logged_in": logged_in, "fallback": fallback, "warning": warning,
            "model": (tab.get(provider) or {}).get(level, ""),
            # modelo de cada nível na assinatura em uso: a página mostra no seletor de nível (assistente, cargo)
            "models": dict(tab.get(provider) or {}),
            "models_by_provider": {k: dict(v) for k, v in tab.items()},
            "levels": [{"id": k, "name": v} for k, v in level_names().items()],
            "providers": [{"id": k, "name": v["name"], "sign_in": v["sign_in"]} for k, v in PROVIDERS.items()],
            "login": {k: login.get(k) for k in ("provider", "state", "url", "code", "error", "started_at") if login.get(k) is not None} or None}


def choose(home: Path, provider: str | None, level: str | None) -> dict:
    global _HOME
    _HOME = home
    saved = store(home)
    if provider is not None:
        if provider not in PROVIDERS:
            return {"ok": False, "error": T("provedor desconhecido")}
        if not is_logged_in(provider, fresh=True):
            return {"ok": False, "error": T("entre na sua conta do {name} primeiro", name=PROVIDERS[provider]["name"])}
        saved["provider"] = provider
    if level is not None:
        if level not in LEVELS:
            return {"ok": False, "error": T("nível desconhecido")}
        saved["level"] = level
    saved.setdefault("provider", "claude")
    saved.setdefault("level", "balanced")
    saved["logged_in"] = [p for p in PROVIDERS if is_logged_in(p)]
    save(home / "thinker.json", saved)
    return {"ok": True, "provider": saved["provider"], "level": saved["level"], "logged_in": saved["logged_in"]}


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def start_login(home: Path, provider: str) -> dict:
    """Começa o login por código de dispositivo em segundo plano e devolve logo; a página acompanha pelo status."""
    global _HOME
    _HOME = home
    if provider != "chatgpt":
        return {"ok": False, "error": T("o Claude entra pelo login do Claude Code, feito na instalação")}
    current = load(home / "thinker.login.json", {})
    if current.get("state") in ("preparing", "waiting") and alive(current.get("pid")):
        return {"ok": True, "login": current}
    log_file = home / "logs" / "thinker-login.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([hermes_bin(), "auth", "add", "openai-codex", "--type", "oauth", "--no-browser", "--label", "whitebeard-os"],
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PYTHONUNBUFFERED": "1"})
    state = {"provider": provider, "state": "preparing", "pid": proc.pid, "started_at": int(time.time())}
    save(home / "thinker.login.json", state)
    for _ in range(30):  # até ~15 s pelo endereço e o código
        time.sleep(0.5)
        state = refresh_login(home)
        if state.get("state") != "preparing":
            break
    return {"ok": state.get("state") != "error", "login": {k: v for k, v in state.items() if k != "pid"}, **({"error": state.get("error")} if state.get("error") else {})}


def refresh_login(home: Path) -> dict:
    """Lê a saída do processo: endereço e código; ao terminar, confere no Hermes e registra o provedor em logados."""
    global _HOME
    _HOME = home
    state = load(home / "thinker.login.json", {})
    if not state:
        return {}
    try:
        txt = ANSI.sub("", (home / "logs" / "thinker-login.log").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        txt = ""
    m_url = re.search(r"(https://\S+/codex/device\S*)", txt)
    m_code = re.search(r"Enter this code:\s*\n\s*([A-Z0-9][A-Z0-9-]{3,})", txt)
    if m_url:
        state["url"] = m_url.group(1)
    if m_code:
        state["code"] = m_code.group(1)
    if state.get("state") == "preparing" and m_url and m_code:
        state["state"] = "waiting"
    if not alive(state.get("pid")) and state.get("state") in ("preparing", "waiting"):
        if is_logged_in(state.get("provider", "chatgpt"), fresh=True):
            state["state"] = "connected"
            saved = store(home)
            saved["logged_in"] = sorted(set(saved.get("logged_in") or []) | {state.get("provider", "chatgpt")})
            saved.setdefault("provider", "claude")
            saved.setdefault("level", "balanced")
            save(home / "thinker.json", saved)
        else:
            last_lines = [l.strip() for l in txt.splitlines() if l.strip()][-1:] or [LOGIN_INCOMPLETE_ERROR]
            state["state"] = "error"
            state["error"] = (LOGIN_EXPIRED_ERROR if "cancel" in txt.lower() or "expired" in txt.lower()
                              else last_lines[0][:200])
    save(home / "thinker.login.json", state)
    return login_for_display(state)


def cancel_login(home: Path) -> dict:
    global _HOME
    _HOME = home
    state = load(home / "thinker.login.json", {})
    if alive(state.get("pid")):
        try:
            os.killpg(int(state["pid"]), signal.SIGTERM)
        except OSError:
            pass
    state["state"] = "cancelled"
    save(home / "thinker.login.json", state)
    return {"ok": True}


def key_is_valid(key: str) -> tuple[bool, str]:
    """Confere a chave no OpenRouter (GET /api/v1/key). (ok, motivo). A chave não entra em mensagem nem em log."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}", "User-Agent": "whitebeard-os"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return (r.status == 200, "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, T("o OpenRouter recusou a chave; confira em openrouter.ai/keys e cole de novo")
        return False, T("o OpenRouter respondeu {code}; tente de novo", code=e.code)
    except (OSError, ValueError):
        return False, T("não consegui falar com o OpenRouter; confira a internet e tente de novo")


def connect_key(home: Path, provider: str, key: str) -> dict:
    """Conecta um provedor que entra por chave de API (OpenRouter): confere a chave e a guarda no pool do Hermes."""
    global _HOME
    _HOME = home
    if PROVIDERS.get(provider, {}).get("sign_in") != "key":
        return {"ok": False, "error": T("este provedor não entra por chave")}
    key = (key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,300}", key):
        return {"ok": False, "error": T("cole a chave do OpenRouter (começa com sk-or-)")}
    ok, reason = key_is_valid(key)
    if not ok:
        return {"ok": False, "error": reason}
    hp = PROVIDERS[provider]["hermes"]
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        # chave nova substitui a anterior; sem terminal, o Hermes lê a chave da entrada padrão
        _remove_keys(provider)
        r = subprocess.run([hermes_bin(), "auth", "add", hp, "--type", "api-key", "--label", LABEL, "--priority", "0"], input=key + "\n",
                           capture_output=True, text=True, timeout=40, check=False, env=env, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": T("não consegui guardar a chave no Hermes: {error_class}", error_class=e.__class__.__name__)}
    if r.returncode != 0 or not is_logged_in(provider, fresh=True):
        return {"ok": False, "error": T("o Hermes não aceitou a chave; atualize o Whitebeard OS e tente de novo")}
    saved = store(home)
    saved["logged_in"] = sorted(set(saved.get("logged_in") or []) | {provider})
    saved.setdefault("provider", "claude")
    saved.setdefault("level", "balanced")
    save(home / "thinker.json", saved)
    return {"ok": True, "provider": saved["provider"], "logged_in": saved["logged_in"]}


def logout(home: Path, provider: str) -> dict:
    """Tira a assinatura do pool do Hermes (ChatGPT ou OpenRouter; a do Claude é a do Claude Code). Se era a
    principal, o Claude volta a ser."""
    global _HOME
    _HOME = home
    if PROVIDERS.get(provider, {}).get("sign_in") not in ("code", "key"):
        return {"ok": False, "error": T("a conta do Claude é a do Claude Code; saia por lá")}
    try:
        if PROVIDERS[provider]["sign_in"] == "key":
            _remove_keys(provider)
        else:
            subprocess.run([hermes_bin(), "auth", "logout", PROVIDERS[provider]["hermes"]], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": T("não consegui sair: {error_class}", error_class=e.__class__.__name__)}
    saved = store(home)
    saved["logged_in"] = [p for p in (saved.get("logged_in") or []) if p != provider]
    is_logged_in(provider, fresh=True)
    if saved.get("provider") == provider:
        saved["provider"] = "claude"
    save(home / "thinker.json", saved)
    return {"ok": True, "provider": saved.get("provider", "claude")}
