#!/usr/bin/env python3
"""jarvis_setup.py — configuração depois da instalação, sem terminal interativo.

Quem usa: o JARVIS Web (página PRIMEIROS PASSOS) e o bootstrap.sh (--telegram). Só biblioteca padrão,
para rodar com o python3 do sistema a partir de um serviço. Cada comando imprime UMA linha de JSON.

Segredos nunca passam por argumento nem saem na resposta: entram por stdin e ficam em
$JARVIS_HOME/secrets.env (modo 600). O estado diz só se cada um está preenchido.

  jarvis_setup.py --home ~/.jarvis status
  jarvis_setup.py --home ~/.jarvis set-secret NOME            valor em stdin; vazio limpa
  jarvis_setup.py --home ~/.jarvis telegram-token             token em stdin; valida em getMe e guarda
  jarvis_setup.py --home ~/.jarvis telegram-wait [--seconds N] [--since EPOCH]
                                                              espera a primeira mensagem ao bot e guarda o chat
  jarvis_setup.py --home ~/.jarvis telegram-off               a pessoa não quer Telegram (não perguntar de novo)
  jarvis_setup.py --home ~/.jarvis done                       marca os primeiros passos como concluídos
  jarvis_setup.py --home ~/.jarvis update-check [--max-age S] [--force]
                                                              há versão nova do kernel/JARVIS Web no GitHub?
                                                              (resposta da rede guardada em update.json por S segundos)
  jarvis_setup.py --home ~/.jarvis update-run                 roda o install.sh desacoplado (sobrevive ao reinício
                                                              do JARVIS Web); log em logs/update.log
  jarvis_setup.py --home ~/.jarvis update-status              andamento/resultado da atualização em curso ou da última
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# o kernel gera ou deriva estes; o Telegram tem o seu próprio passo
HIDDEN = {"GBRAIN_MCP_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"}


def out(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0 if obj.get("ok", True) else 1


def read_env(path: Path) -> dict[str, str]:
    """NOME=valor de um arquivo gerado pelo kernel (valores entre aspas simples ou nus)."""
    vals: dict[str, str] = {}
    if not path.is_file():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            parts = shlex.split(v)
        except ValueError:
            parts = [v]
        vals[k.strip()] = parts[0] if parts else ""
    return vals


def set_secret(path: Path, name: str, value: str) -> None:
    """Mesma regra do bootstrap: substitui a linha NOME=..., ou acrescenta; valor entre aspas simples."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    quoted = ("'" + value.replace("'", "'\\''") + "'") if value else ""
    new_line = f"{name}={quoted}"
    text2, n = re.subn(r"(?m)^" + re.escape(name) + r"=.*$", lambda _: new_line, text)
    if not n:
        text2 = text + ("" if text.endswith("\n") or not text else "\n") + new_line + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text2, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def stdin_value() -> str:
    data = sys.stdin.read()
    # uma linha: o \n final vem do terminal ou do echo, não é parte do valor
    if data.endswith("\n"):
        data = data[:-1]
    if data.endswith("\r"):
        data = data[:-1]
    return data.strip()


def declared_secrets(home: Path) -> list[dict]:
    try:
        return json.loads((home / "render" / "secrets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- status
def pending_apply(home: Path) -> bool:
    """Há algo gravado (segredo ou assistente) e ainda não colocado em uso: secrets.env ou assistentes.json
    mais novos que a marca setup.applied (que o bootstrap toca ao aplicar)."""
    try:
        aplicado = (home / "setup.applied").stat().st_mtime
    except OSError:
        return False
    for f in (home / "secrets.env", store_path(home), home / "escritorio.json"):
        try:
            if f.stat().st_mtime > aplicado + 1:
                return True
        except OSError:
            pass
    return False


ASSISTENTE_NOME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
ASSISTENTE_TIPOS = ("mim", "equipe", "cliente", "rotinas")
# Onde o assistente fala e os nomes dos segredos dele (espelho de CANAIS no jarvis_render.py)
ASSISTENTE_CANAIS = {
    "telegram": {"token": "TELEGRAM_BOT_TOKEN", "pessoas": "TELEGRAM_ALLOWED_USERS", "grupos": "TELEGRAM_GROUP_ALLOWED_CHATS"},
    "discord": {"token": "DISCORD_BOT_TOKEN", "pessoas": "DISCORD_ALLOWED_USERS", "grupos": "DISCORD_ALLOWED_CHANNELS"},
    "whatsapp": {"token": None, "pessoas": "WHATSAPP_ALLOWED_USERS", "grupos": "WHATSAPP_GROUP_ALLOWED_USERS"},
}
WHATSAPP_MODOS = ("bot", "self-chat")


def canal_de(a: dict) -> str:
    c = a.get("canal") or "telegram"
    return c if c in ASSISTENTE_CANAIS else "telegram"


def chaves_assistente(nome: str, canal: str) -> dict[str, str]:
    c = ASSISTENTE_CANAIS.get(canal) or ASSISTENTE_CANAIS["telegram"]
    return {k: (f"{v}_{sufixo(nome)}" if v else "") for k, v in c.items()}


def hermes_home_de(home: Path) -> Path:
    return Path(read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes"))


def whatsapp_sessao(home: Path, nome: str) -> Path:
    return home / "assistentes" / nome / "whatsapp" / "session"


def whatsapp_estado_path(home: Path, nome: str) -> Path:
    return home / "assistentes" / f"{nome}.whatsapp.json"


def whatsapp_telefone(sessao: Path) -> str:
    try:
        me = json.loads((sessao / "creds.json").read_text(encoding="utf-8")).get("me") or {}
        return str(me.get("id", "")).split(":", 1)[0].split("@", 1)[0]
    except (OSError, ValueError, AttributeError):
        return ""


def whatsapp_ponte_dir(home: Path) -> Path:
    return hermes_home_de(home) / "hermes-agent" / "scripts" / "whatsapp-bridge"


def whatsapp_ponte_pronta(home: Path) -> str | None:
    """Garante a ponte (Node) do Hermes com as dependências instaladas. Devolve o erro, ou None."""
    ponte = whatsapp_ponte_dir(home)
    if not (ponte / "bridge.js").is_file():
        return "a ponte do WhatsApp do Hermes não está instalada neste computador"
    if (ponte / "node_modules").is_dir():
        return None
    npm = shutil.which("npm")
    if not npm or not shutil.which("node"):
        return "node/npm não encontrados; o kernel fixa o Node na instalação (rode o instalador de novo)"
    try:
        r = subprocess.run([npm, "install", "--omit=dev", "--no-audit", "--no-fund", "--silent"], cwd=str(ponte), capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return f"não consegui preparar a ponte do WhatsApp ({e.__class__.__name__})"
    if r.returncode != 0:
        return "não consegui preparar a ponte do WhatsApp (npm install falhou; veja a internet e tente de novo)"
    return None


def whatsapp_pareamento_vivo(home: Path, nome: str) -> dict | None:
    """Estado do pareamento em curso (arquivo escrito por jarvis_whatsapp_pair.py), ou None se não há."""
    try:
        d = json.loads(whatsapp_estado_path(home, nome).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = d.get("pid")
    vivo = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            vivo = True
        except OSError:
            vivo = False
    if d.get("estado") == "cancelado":
        return None
    if d.get("estado") in ("preparando", "esperando", "qr") and not vivo:
        d["estado"] = "erro" if d.get("estado") != "qr" else "expirado"
        d["erro"] = d.get("erro") or "o pareamento parou; gere outro QR"
    return d


def whatsapp_parar_pareamento(home: Path, nome: str) -> None:
    d = whatsapp_pareamento_vivo(home, nome) or {}
    pid = d.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 15)
            for _ in range(20):  # o processo escreve "cancelado" e sai; só então o arquivo pode ir
                time.sleep(0.25)
                os.kill(pid, 0)
        except OSError:
            pass
    whatsapp_estado_path(home, nome).unlink(missing_ok=True)


def apagar_segredos_assistente(home: Path, nome: str) -> None:
    """Esquece token e listas do assistente em todos os canais (revoga o acesso); a sessão do WhatsApp também."""
    env = read_env(home / "secrets.env")
    for canal in ASSISTENTE_CANAIS:
        for k in chaves_assistente(nome, canal).values():
            if k and k in env:
                set_secret(home / "secrets.env", k, "")
    whatsapp_parar_pareamento(home, nome)
    shutil.rmtree(whatsapp_sessao(home, nome), ignore_errors=True)


def store_path(home: Path) -> Path:
    return home / "assistentes.json"


def load_store(home: Path) -> list[dict]:
    """Assistentes criados pelo JARVIS Web (fora do jarvis.yaml, que pode ser o do kernel ou de um pacote)."""
    try:
        data = json.loads(store_path(home).read_text(encoding="utf-8"))
        return [a for a in data if isinstance(a, dict) and a.get("nome")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_store(home: Path, items: list[dict]) -> None:
    store_path(home).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sufixo(nome: str) -> str:
    return nome.upper().replace("-", "_")


def nomes_vivos(home: Path, nomes: object, chave: str) -> list[str]:
    """Nomes das pessoas/grupos reconhecidos cujo id ainda está na lista do secrets.env (ordem da lista)."""
    ids = [x for x in read_env(home / "secrets.env").get(chave, "").split(",") if x]
    mapa = nomes if isinstance(nomes, dict) else {}
    return [mapa.get(i) or "(sem nome)" for i in ids]


def hermes_profile_dir(home: Path, nome: str) -> Path:
    hermes_home = read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(hermes_home) / "profiles" / nome


def limpar_esperas_velhas(home: Path) -> None:
    """Perfil estacionado por uma espera que a página abandonou (PARAR, aba fechada): volta depois de 5 min."""
    base = hermes_profile_dir(home, "x").parent
    if not base.is_dir():
        return
    for marca in base.glob("*/.whitebeard-espera"):
        try:
            if time.time() - int(marca.read_text(encoding="utf-8").strip() or "0") > 300:
                marca.unlink(missing_ok=True)
                (marca.parent / "gateway.parked").unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def assistentes_status(home: Path, filled: dict[str, bool]) -> list[dict]:
    """Assistentes do Telegram: os criados pelo JARVIS Web (loja) e os do tenant (catálogo do render), com o que
    falta a cada um. `aplicado`: já virou perfil do Hermes (está no catálogo renderizado)."""
    try:
        cat = json.loads((home / "render" / "hermes" / "assistentes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cat = []
    por_nome = {a.get("nome"): a for a in cat if isinstance(a, dict)}
    loja = load_store(home)
    removidos = {s.get("nome") for s in loja if s.get("removido")}
    loja = [s for s in loja if not s.get("removido")]
    nomes_loja = {s.get("nome") for s in loja}
    out_: list[dict] = []
    vistos: set[str] = set()
    for a in loja + [c for c in cat if c.get("nome") not in nomes_loja]:
        nome = a.get("nome", "")
        if not nome or nome in vistos or nome in removidos:
            continue
        vistos.add(nome)
        tipo = a.get("tipo", "")
        canal = canal_de(a)
        ch = chaves_assistente(nome, canal)
        # a loja é o que a pessoa decidiu (edição ainda não aplicada inclusive); o catálogo só completa
        cat_a = por_nome.get(nome) or {}
        wa = None
        if canal == "whatsapp":
            sess = whatsapp_sessao(home, nome)
            par = whatsapp_pareamento_vivo(home, nome) or {}
            wa = {"modo": a.get("whatsapp_modo") or "bot", "pareado": (sess / "creds.json").is_file(), "telefone": whatsapp_telefone(sess),
                  "pareando": par.get("estado") if par.get("estado") in ("preparando", "esperando", "qr") else None}
        out_.append({
            "nome": nome, "tipo": tipo, "canal": canal, "descricao": a.get("descricao", ""), "bot": a.get("bot", "") or cat_a.get("bot", ""),
            "app_id": a.get("app_id", ""), "whatsapp": wa,
            "skills": a.get("skills") if a.get("skills") is not None else (cat_a.get("skills") or []),
            "ajustes": {"computador": bool((a.get("ajustes") or {}).get("computador")), "anotar": bool((a.get("ajustes") or {}).get("anotar"))},
            "origem": "web" if nome in nomes_loja else "tenant",
            "do_pacote": bool(a.get("do_pacote")) or (nome not in nomes_loja),
            "token": bool(wa["pareado"]) if wa else bool(filled.get(ch["token"], False)),
            "pessoas": bool(filled.get(ch["pessoas"], False)) if tipo in ("mim", "equipe") else None,
            "grupos": bool(filled.get(ch["grupos"], False)) if tipo in ("equipe", "cliente") else None,
            "aplicado": nome in por_nome and (cat_a.get("canal") or "telegram") == canal,
            "pessoas_nomes": nomes_vivos(home, a.get("pessoas_nomes"), ch["pessoas"]),
            "grupos_nomes": nomes_vivos(home, a.get("grupos_nomes"), ch["grupos"]),
            "segredos": {"token": ch["token"] or None,
                         "pessoas": ch["pessoas"] if tipo in ("mim", "equipe") else None,
                         "grupos": ch["grupos"] if tipo in ("equipe", "cliente") else None},
        })
    return out_


# ---------------------------------------------------------------- assistentes (criar, token, reconhecer, remover)
def cmd_assistente_add(home: Path) -> int:
    raw = stdin_value()
    try:
        dados = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "erro": "dados inválidos"})
    nome = str(dados.get("nome") or "").strip().lower()
    tipo = str(dados.get("tipo") or "").strip()
    canal = str(dados.get("canal") or "telegram").strip()
    descricao = str(dados.get("descricao") or "").strip()[:400]
    if not ASSISTENTE_NOME_RE.match(nome):
        return out({"ok": False, "erro": "nome: só letras minúsculas, números e hífen, começando por letra (2 a 31 caracteres)"})
    if tipo not in ASSISTENTE_TIPOS:
        return out({"ok": False, "erro": "tipo desconhecido"})
    if canal not in ASSISTENTE_CANAIS:
        return out({"ok": False, "erro": "canal desconhecido"})
    modo = str(dados.get("whatsapp_modo") or "bot")
    if canal == "whatsapp" and modo not in WHATSAPP_MODOS:
        return out({"ok": False, "erro": "modo do WhatsApp desconhecido"})
    if nome == "default":
        return out({"ok": False, "erro": "esse nome é reservado"})
    itens = load_store(home)
    if any(a.get("nome") == nome and not a.get("removido") for a in itens) or (
        (home / "render" / "hermes" / "assistentes" / nome).is_dir() and not any(a.get("nome") == nome and a.get("removido") for a in itens)
    ):
        return out({"ok": False, "erro": f"já existe um assistente chamado {nome}"})
    itens = [a for a in itens if a.get("nome") != nome]
    itens.append({"nome": nome, "tipo": tipo, "canal": canal, "descricao": descricao, "criado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  **({"whatsapp_modo": modo} if canal == "whatsapp" else {})})
    save_store(home, itens)
    return out({"ok": True, "assistente": {"nome": nome, "tipo": tipo, "canal": canal, "descricao": descricao}})


def no_catalogo(home: Path, nome: str) -> dict | None:
    try:
        for a in json.loads((home / "render" / "hermes" / "assistentes.json").read_text(encoding="utf-8")):
            if a.get("nome") == nome:
                return a
    except (OSError, ValueError):
        pass
    return None


def cmd_assistente_remove(home: Path, nome: str) -> int:
    itens = load_store(home)
    na_loja = any(a.get("nome") == nome and not a.get("removido") for a in itens)
    do_pacote = (no_catalogo(home, nome) or {}).get("do_pacote") or (not na_loja and no_catalogo(home, nome) is not None)
    if not na_loja and not do_pacote:
        return out({"ok": False, "erro": "assistente desconhecido"})
    itens = [a for a in itens if a.get("nome") != nome]
    if do_pacote:
        # o pacote continua a declará-lo; a marca faz o render ignorá-lo (e o bootstrap estaciona o perfil)
        itens.append({"nome": nome, "removido": True})
    save_store(home, itens)
    # revoga o acesso: token e listas somem deste computador
    apagar_segredos_assistente(home, nome)
    return out({"ok": True})


def cmd_assistente_update(home: Path, nome: str) -> int:
    """Edita descrição, tipo e ajustes. Um assistente do pacote passa a ter uma cópia na loja, que vale por cima."""
    raw = stdin_value()
    try:
        dados = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "erro": "dados inválidos"})
    itens = load_store(home)
    atual = next((a for a in itens if a.get("nome") == nome and not a.get("removido")), None)
    if atual is None:
        base = no_catalogo(home, nome)
        if base is None:
            return out({"ok": False, "erro": "assistente desconhecido"})
        atual = {"nome": nome, "tipo": base.get("tipo", ""), "canal": base.get("canal") or "telegram", "descricao": base.get("descricao", ""),
                 "skills": base.get("skills") or [], "ajustes": base.get("ajustes") or {}, "do_pacote": True}
        itens.append(atual)
    if "canal" in dados:
        canal = str(dados.get("canal") or "telegram")
        if canal not in ASSISTENTE_CANAIS:
            return out({"ok": False, "erro": "canal desconhecido"})
        if canal != canal_de(atual):
            # trocar de canal é recomeçar: o bot, as pessoas e os grupos do canal antigo não valem no novo
            apagar_segredos_assistente(home, nome)
            atual["canal"] = canal
            for k in ("bot", "app_id", "pessoas_nomes", "grupos_nomes"):
                atual.pop(k, None)
    if "whatsapp_modo" in dados and canal_de(atual) == "whatsapp":
        modo = str(dados.get("whatsapp_modo") or "bot")
        if modo not in WHATSAPP_MODOS:
            return out({"ok": False, "erro": "modo do WhatsApp desconhecido"})
        if modo != (atual.get("whatsapp_modo") or "bot"):
            # trocar o modo é parear de novo (outro número)
            apagar_segredos_assistente(home, nome)
            atual.pop("pessoas_nomes", None); atual.pop("grupos_nomes", None)
        atual["whatsapp_modo"] = modo
    if "descricao" in dados:
        atual["descricao"] = str(dados.get("descricao") or "").strip()[:400]
    if "tipo" in dados:
        tipo = str(dados.get("tipo") or "")
        if tipo not in ASSISTENTE_TIPOS:
            return out({"ok": False, "erro": "tipo desconhecido"})
        if tipo != atual.get("tipo"):
            atual["tipo"] = tipo
            atual.pop("skills", None)  # skills padrão do tipo novo
            atual["ajustes"] = {}
    if "ajustes" in dados:
        aj = dados.get("ajustes") or {}
        if atual.get("tipo") in ("cliente", "rotinas") and any(aj.values()):
            return out({"ok": False, "erro": "este tipo não aceita ajustes"})
        atual["ajustes"] = {k: bool(aj.get(k)) for k in ("computador", "anotar") if k in aj}
    if "skills" in dados and isinstance(dados.get("skills"), list):
        atual["skills"] = [str(s) for s in dados["skills"] if re.match(r"^[a-z][a-z0-9-]*$", str(s))]
    save_store(home, itens)
    return out({"ok": True, "assistente": {k: atual.get(k) for k in ("nome", "tipo", "canal", "whatsapp_modo", "descricao", "ajustes")}})


def assistente_do_store(home: Path, nome: str) -> dict | None:
    for a in load_store(home):
        if a.get("nome") == nome:
            return None if a.get("removido") else a
    try:
        for a in json.loads((home / "render" / "hermes" / "assistentes.json").read_text(encoding="utf-8")):
            if a.get("nome") == nome:
                return a
    except (OSError, ValueError):
        pass
    return None


def cmd_assistente_token(home: Path, nome: str) -> int:
    a = assistente_do_store(home, nome)
    if not a:
        return out({"ok": False, "erro": "assistente desconhecido"})
    token = stdin_value()
    if not token:
        return out({"ok": False, "erro": "token vazio"})
    canal = canal_de(a)
    ch = chaves_assistente(nome, canal)
    app_id = ""
    if canal == "whatsapp":
        return out({"ok": False, "erro": "o WhatsApp não usa token: leia o QR na página"})
    if canal == "discord":
        try:
            me = discord_api(token, "/users/@me")
            username = str(me.get("username") or "")
            app_id = str((discord_api(token, "/oauth2/applications/@me") or {}).get("id") or "")
        except DiscordError as e:
            if e.code in (401, 403):
                return out({"ok": False, "erro": "o Discord recusou o token (confira em Developer Portal → Bot → Reset Token)"})
            return out({"ok": False, "erro": "não consegui falar com o Discord; confira a internet e tente de novo"})
        onde = "crie outra aplicação no Developer Portal"
    else:
        try:
            me = tg_api(token, "getMe", http_timeout=20)
        except TelegramError as e:
            if e.code in (401, 404):
                return out({"ok": False, "erro": "o Telegram recusou o token (confira em @BotFather)"})
            return out({"ok": False, "erro": "não consegui falar com o Telegram; confira a internet e tente de novo"})
        username = str(me.get("username") or "")
        onde = "crie outro no @BotFather"
    # o mesmo bot não pode ser dois assistentes (nem o do dono)
    prefixo = ASSISTENTE_CANAIS[canal]["token"]
    for k, v in read_env(home / "secrets.env").items():
        if k.startswith(prefixo) and k != ch["token"] and v == token:
            return out({"ok": False, "erro": f"esse bot já é usado por outro assistente; {onde}"})
    set_secret(home / "secrets.env", ch["token"], token)
    itens = load_store(home)
    for it in itens:
        if it.get("nome") == nome:
            it["bot"] = username
            if app_id:
                it["app_id"] = app_id
    save_store(home, itens)
    return out({"ok": True, "bot": username, "app_id": app_id, "canal": canal})


def lista_add(home: Path, chave: str, valor: str) -> None:
    atual = [x for x in read_env(home / "secrets.env").get(chave, "").split(",") if x]
    if valor not in atual:
        atual.append(valor)
    set_secret(home / "secrets.env", chave, ",".join(atual))


class DiscordError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(msg)
        self.code = code


def discord_api(token: str, path: str) -> dict:
    """GET na API do Discord com o token do bot; `code` é o HTTP em caso de recusa."""
    conn = TelegramConnection("discord.com", timeout=20)
    try:
        conn.request("GET", "/api/v10" + path, headers={"Authorization": f"Bot {token}", "User-Agent": "whitebeard-os (jarvis_setup)"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise DiscordError(resp.status, f"HTTP {resp.status}")
        return json.loads(body)
    except DiscordError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise DiscordError(None, str(e)) from e
    finally:
        conn.close()


def hermes_python(home: Path) -> Path | None:
    """O Python do venv do Hermes (tem websockets/aiohttp): é com ele que a espera do Discord roda."""
    hermes_home = read_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes")
    p = Path(hermes_home) / "hermes-agent" / "venv" / "bin" / "python"
    return p if p.is_file() else None


def anota_nome(home: Path, nome: str, campo: str, chave: str, rotulo: str) -> None:
    """Guarda o nome (não o id) de quem foi reconhecido, para a página mostrar; a loja é a dona."""
    itens = load_store(home)
    for it in itens:
        if it.get("nome") == nome:
            nomes = it.setdefault(campo, {})
            if isinstance(nomes, dict):
                nomes[chave] = rotulo
    save_store(home, itens)


def assistente_wait_discord(home: Path, nome: str, tipo: str, token: str, seconds: int, since: int,
                            aceita_pessoa: bool, aceita_grupo: bool) -> int:
    """Discord: conversa direta reconhece a pessoa; menção num canal reconhece o canal (e quem mencionou, para
    a equipe). O gateway do Hermes pode continuar ligado: o Discord aceita mais de uma sessão do mesmo bot."""
    py = hermes_python(home)
    if not py:
        return out({"ok": False, "erro": "o Hermes não está instalado neste computador"})
    ch = chaves_assistente(nome, "discord")
    script = Path(__file__).resolve().parent / "jarvis_discord_wait.py"
    env = dict(os.environ, DISCORD_BOT_TOKEN=token)
    try:
        r = subprocess.run([str(py), str(script), str(seconds), str(since)], capture_output=True, text=True, timeout=seconds + 20, env=env, check=False)
        res = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        res = {}
    if not res:
        return out({"ok": False, "erro": "não consegui falar com o Discord; tente de novo"})
    if not res.get("ok"):
        return out(res)
    if res.get("grupo_id") and aceita_grupo:
        lista_add(home, ch["grupos"], str(res["grupo_id"]))
        anota_nome(home, nome, "grupos_nomes", str(res["grupo_id"]), str(res.get("grupo") or ""))
        if aceita_pessoa and res.get("pessoa_id"):
            lista_add(home, ch["pessoas"], str(res["pessoa_id"]))
            anota_nome(home, nome, "pessoas_nomes", str(res["pessoa_id"]), str(res.get("pessoa") or ""))
        return out({"ok": True, "grupo": res.get("grupo", ""), "pessoa": res.get("pessoa", "") if aceita_pessoa else ""})
    if not res.get("grupo_id") and aceita_pessoa and res.get("pessoa_id"):
        lista_add(home, ch["pessoas"], str(res["pessoa_id"]))
        anota_nome(home, nome, "pessoas_nomes", str(res["pessoa_id"]), str(res.get("pessoa") or ""))
        return out({"ok": True, "pessoa": res.get("pessoa", "")})
    # chegou algo que o tipo não aceita (cliente em conversa direta; mim num canal): segue esperando na próxima fatia
    return out({"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True})


def estaciona_perfil(home: Path, nome: str) -> None:
    """Marca o perfil como estacionado enquanto a página usa a sessão (o gateway solta a ponte em segundos)."""
    perfil = hermes_profile_dir(home, nome)
    if perfil.is_dir() and not (perfil / "gateway.parked").exists():
        (perfil / "gateway.parked").write_text("espera do Whitebeard OS\n", encoding="utf-8")
        (perfil / ".whitebeard-espera").write_text(str(int(time.time())) + "\n", encoding="utf-8")
        time.sleep(4)


def desestaciona_perfil(home: Path, nome: str) -> None:
    perfil = hermes_profile_dir(home, nome)
    if (perfil / ".whitebeard-espera").exists():
        (perfil / ".whitebeard-espera").unlink(missing_ok=True)
        (perfil / "gateway.parked").unlink(missing_ok=True)


def cmd_assistente_whatsapp_parear(home: Path, nome: str) -> int:
    """Começa o pareamento por QR em segundo plano; a página acompanha por whatsapp-status."""
    a = assistente_do_store(home, nome)
    if not a or canal_de(a) != "whatsapp":
        return out({"ok": False, "erro": "assistente desconhecido ou não é do WhatsApp"})
    raw = stdin_value()
    try:
        dados = json.loads(raw or "{}")
    except ValueError:
        dados = {}
    modo = str(dados.get("modo") or a.get("whatsapp_modo") or "bot")
    if modo not in WHATSAPP_MODOS:
        return out({"ok": False, "erro": "modo do WhatsApp desconhecido"})
    erro = whatsapp_ponte_pronta(home)
    if erro:
        return out({"ok": False, "erro": erro})
    whatsapp_parar_pareamento(home, nome)
    sess = whatsapp_sessao(home, nome)
    # parear de novo = sessão nova (o aparelho antigo some do WhatsApp sozinho ao ser substituído)
    shutil.rmtree(sess, ignore_errors=True)
    estaciona_perfil(home, nome)
    itens = load_store(home)
    for it in itens:
        if it.get("nome") == nome:
            it["whatsapp_modo"] = modo
    save_store(home, itens)
    script = Path(__file__).resolve().parent / "jarvis_whatsapp_pair.py"
    estado = whatsapp_estado_path(home, nome)
    estado.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([sys.executable, str(script), "--ponte", str(whatsapp_ponte_dir(home)), "--sessao", str(sess), "--estado", str(estado), "--modo", modo],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        desestaciona_perfil(home, nome)
        return out({"ok": False, "erro": f"não consegui iniciar o pareamento ({e.__class__.__name__})"})
    return out({"ok": True, "estado": "preparando"})


def cmd_assistente_whatsapp_status(home: Path, nome: str) -> int:
    a = assistente_do_store(home, nome)
    if not a or canal_de(a) != "whatsapp":
        return out({"ok": False, "erro": "assistente desconhecido ou não é do WhatsApp"})
    sess = whatsapp_sessao(home, nome)
    par = whatsapp_pareamento_vivo(home, nome) or {}
    pareado = (sess / "creds.json").is_file() and par.get("estado") in (None, "conectado")
    if par.get("estado") in ("conectado", "erro", "expirado"):
        desestaciona_perfil(home, nome)
    return out({"ok": True, "pareado": pareado, "telefone": whatsapp_telefone(sess) if pareado else "", "nome": par.get("nome", ""),
                "estado": par.get("estado") or ("conectado" if pareado else "nenhum"), "qr": par.get("qr") if par.get("estado") == "qr" else None,
                "erro": par.get("erro"), "modo": a.get("whatsapp_modo") or "bot"})


def cmd_assistente_whatsapp_desparear(home: Path, nome: str) -> int:
    a = assistente_do_store(home, nome)
    if not a or canal_de(a) != "whatsapp":
        return out({"ok": False, "erro": "assistente desconhecido ou não é do WhatsApp"})
    whatsapp_parar_pareamento(home, nome)
    shutil.rmtree(whatsapp_sessao(home, nome), ignore_errors=True)
    desestaciona_perfil(home, nome)
    return out({"ok": True})


def assistente_wait_whatsapp(home: Path, nome: str, a: dict, seconds: int, since: int, aceita_pessoa: bool, aceita_grupo: bool) -> int:
    """WhatsApp: sobe a ponte com a sessão do assistente numa porta de espera e lê as mensagens que chegam:
    conversa direta reconhece o número; mensagem num grupo reconhece o grupo. O perfil fica estacionado enquanto
    isso (a mesma sessão não pode estar em duas pontes)."""
    erro = whatsapp_ponte_pronta(home)
    if erro:
        return out({"ok": False, "erro": erro})
    ch = chaves_assistente(nome, "whatsapp")
    modo = a.get("whatsapp_modo") or "bot"
    sess = whatsapp_sessao(home, nome)
    node = shutil.which("node")
    porta = 3998
    estaciona_perfil(home, nome)
    env = dict(os.environ, WHATSAPP_MODE=modo, WHATSAPP_DM_POLICY="pairing", WHATSAPP_GROUP_POLICY="open")
    try:
        proc = subprocess.Popen([node, str(whatsapp_ponte_dir(home) / "bridge.js"), "--session", str(sess), "--mode", modo, "--port", str(porta)],
                                cwd=str(whatsapp_ponte_dir(home)), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        desestaciona_perfil(home, nome)
        return out({"ok": False, "erro": f"não consegui subir a ponte do WhatsApp ({e.__class__.__name__})"})

    def ponte_get(path: str):
        conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{porta}"})
            r = conn.getresponse()
            return json.loads(r.read()) if r.status == 200 else None
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            conn.close()

    res = None
    deadline = time.time() + max(5, min(seconds, 300))
    try:
        while time.time() < deadline and proc.poll() is None:
            msgs = ponte_get("/messages")
            for m in msgs or []:
                chat = str(m.get("chatId") or "")
                quem = str(m.get("senderId") or chat)
                if m.get("isGroup") or chat.endswith("@g.us"):
                    if not aceita_grupo:
                        continue
                    rotulo = chat.split("@", 1)[0]
                    meta = ponte_get(f"/chat/{chat}")
                    if isinstance(meta, dict) and meta.get("name"):
                        rotulo = str(meta["name"])
                    lista_add(home, ch["grupos"], chat)
                    anota_nome(home, nome, "grupos_nomes", chat, rotulo)
                    res = {"ok": True, "grupo": rotulo}
                    break
                if aceita_pessoa and quem:
                    numero = quem.split(":", 1)[0].split("@", 1)[0]
                    if not numero.isdigit():
                        continue
                    lista_add(home, ch["pessoas"], numero)
                    anota_nome(home, nome, "pessoas_nomes", numero, f"+{numero}")
                    res = {"ok": True, "pessoa": f"+{numero}"}
                    break
            if res:
                break
            time.sleep(2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if res or time.time() - since >= 170:
            desestaciona_perfil(home, nome)
    return out(res or {"ok": False, "erro": "nenhuma mensagem chegou", "tempo_esgotado": True})


def cmd_assistente_wait(home: Path, nome: str, seconds: int, since: int) -> int:
    """Espera a primeira mensagem ao bot deste assistente: uma pessoa (conversa direta) vira "quem fala com
    ele"; um grupo (mensagem no grupo, ou o bot adicionado a ele) vira "grupo dele". O que o tipo não aceita
    é ignorado (cliente não ganha pessoas; mim não ganha grupos)."""
    a = assistente_do_store(home, nome)
    if not a:
        return out({"ok": False, "erro": "assistente desconhecido"})
    tipo = a.get("tipo", "")
    canal = canal_de(a)
    ch = chaves_assistente(nome, canal)
    token = read_env(home / "secrets.env").get(ch["token"], "") if ch["token"] else ("pareado" if (whatsapp_sessao(home, nome) / "creds.json").is_file() else "")
    if not token:
        return out({"ok": False, "erro": "sem token do bot deste assistente" if ch["token"] else "o WhatsApp deste assistente ainda não foi pareado"})
    aceita_pessoa = tipo in ("mim", "equipe")
    aceita_grupo = tipo in ("equipe", "cliente")
    seconds = max(5, min(seconds, 300))
    if canal == "discord":
        return assistente_wait_discord(home, nome, tipo, token, seconds, since, aceita_pessoa, aceita_grupo)
    if canal == "whatsapp":
        return assistente_wait_whatsapp(home, nome, a, seconds, since, aceita_pessoa, aceita_grupo)
    up = sufixo(nome)
    deadline = time.time() + seconds
    offset = 0
    stopped_gateway = False
    # Se o gateway já serve este bot (assistente aplicado), ele consome as mensagens antes de nós e descarta as
    # de grupos ainda não permitidos: estaciona só este perfil (gateway.parked) enquanto a espera dura. O
    # perfil volta quando algo é reconhecido, quando a espera toda (desde `since`) termina, ou no próximo
    # colocar em uso. A página espera em fatias; a marca .whitebeard-espera lembra que fomos nós.
    perfil = hermes_profile_dir(home, nome)
    parked = perfil / "gateway.parked"
    marca_espera = perfil / ".whitebeard-espera"
    if perfil.is_dir() and read_env(perfil / ".env").get("TELEGRAM_BOT_TOKEN") == token:
        if not parked.exists():
            parked.write_text("espera do JARVIS Web\n", encoding="utf-8")
            marca_espera.write_text(str(int(time.time())) + "\n", encoding="utf-8")
            # o gateway solta o bot em alguns segundos; espera até o Telegram parar de dar 409
            for _ in range(15):
                try:
                    tg_api(token, "getUpdates", offset=-1, timeout=0)
                    break
                except TelegramError as e:
                    if e.code != 409:
                        break
                    time.sleep(2)

    def desestaciona() -> None:
        if marca_espera.exists():
            marca_espera.unlink(missing_ok=True)
            parked.unlink(missing_ok=True)

    def anota(campo: str, chave: str, rotulo: str) -> None:
        """Guarda o nome (não o id) de quem foi reconhecido, para a página mostrar; a loja é a dona."""
        itens = load_store(home)
        for it in itens:
            if it.get("nome") == nome:
                nomes = it.setdefault(campo, {})
                if isinstance(nomes, dict):
                    nomes[chave] = rotulo
        save_store(home, itens)

    def reconhece(u: dict):
        m = u.get("message") or u.get("my_chat_member") or {}
        chat = m.get("chat") or {}
        if m.get("date", 0) < since:
            return None
        if chat.get("type") == "private" and aceita_pessoa and (m.get("from") or {}).get("id"):
            sender = m["from"]
            rotulo = str(sender.get("first_name") or sender.get("username") or "")
            lista_add(home, f"TELEGRAM_ALLOWED_USERS_{up}", str(sender["id"]))
            anota("pessoas_nomes", str(sender["id"]), rotulo)
            return {"ok": True, "pessoa": rotulo}
        if chat.get("type") in ("group", "supergroup") and aceita_grupo and chat.get("id"):
            rotulo = str(chat.get("title") or "")
            lista_add(home, f"TELEGRAM_GROUP_ALLOWED_CHATS_{up}", str(chat["id"]))
            anota("grupos_nomes", str(chat["id"]), rotulo)
            return {"ok": True, "grupo": rotulo}
        return None

    def fim(res: dict | None):
        if offset:
            try:
                tg_api(token, "getUpdates", offset=offset, timeout=0)
            except TelegramError:
                pass
        if stopped_gateway:
            hermes_gateway("start")
        # reconheceu, ou a espera inteira da página (3 min desde `since`) acabou: devolve o bot ao gateway
        if res or time.time() - since >= 170:
            desestaciona()
        return out(res or {"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True})

    try:
        last = tg_api(token, "getUpdates", offset=-1, timeout=0, allowed_updates='["message","my_chat_member"]')
        if last:
            offset = last[-1]["update_id"] + 1
            r = reconhece(last[-1])
            if r:
                return fim(r)
    except TelegramError as e:
        if e.code != 409:
            return out({"ok": False, "erro": "não consegui falar com o Telegram"})
    while time.time() < deadline:
        try:
            ups = tg_api(token, "getUpdates", http_timeout=35, offset=offset, allowed_updates='["message","my_chat_member"]',
                         timeout=min(25, max(1, int(deadline - time.time()))))
        except TelegramError as e:
            if e.code == 409:
                if not stopped_gateway:
                    hermes_gateway("stop")
                    stopped_gateway = True
                time.sleep(2)
                continue
            if stopped_gateway:
                hermes_gateway("start")
            return out({"ok": False, "erro": "o Telegram não entregou mensagens; tente de novo"})
        for u in ups:
            offset = u["update_id"] + 1
            r = reconhece(u)
            if r:
                return fim(r)
    return fim(None)


# ---------------------------------------------------------------- Escritório (Paperclip como motor, Hermes como executor)
def escritorio_desejado(home: Path) -> bool | None:
    """O que a pessoa decidiu na página (loja) ou, sem decisão, o que o tenant diz."""
    try:
        loja = json.loads((home / "escritorio.json").read_text(encoding="utf-8"))
        if isinstance(loja, dict) and "ligado" in loja:
            return bool(loja["ligado"])
    except (OSError, ValueError):
        pass
    for cand in (Path((home / "tenant.path").read_text(encoding="utf-8").strip()) if (home / "tenant.path").is_file() else None, home / "jarvis.yaml"):
        try:
            if cand and cand.is_file():
                return re.search(r"^escritorio:\s*\n(?:\s+.*\n)*?\s+ligado:\s*true", cand.read_text(encoding="utf-8"), re.M) is not None
        except OSError:
            continue
    return None


def escritorio_status(home: Path, roles: list[str]) -> dict:
    """Ligado?, aplicado?, o cargo e as tarefas (lidas do Paperclip local, se estiver no ar)."""
    out_: dict = {"disponivel": "core" in roles, "ligado": bool(escritorio_desejado(home)), "aplicado": False, "cargo": None, "tarefas": [], "erro": None}
    try:
        out_["aplicado"] = bool(json.loads((home / "render" / "escritorio.json").read_text(encoding="utf-8")).get("ligado"))
    except (OSError, ValueError):
        pass
    if not out_["aplicado"]:
        return out_
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_escritorio_mcp as esc  # mesmo diretório; só stdlib
    except ImportError:
        return out_
    try:
        cargo = json.loads((home / "escritorio" / "cargo.json").read_text(encoding="utf-8"))
        ag = esc.api("GET", f"/agents/{cargo['id']}")
        out_["cargo"] = {"nome": cargo.get("nome"), "estado": ag.get("status"), "pasta": cargo.get("pasta"), "gasto_mes_centavos": ag.get("spentMonthlyCents")}
    except (OSError, ValueError, RuntimeError, KeyError):
        pass
    try:
        itens = esc.lista(esc.api("GET", f"/companies/{esc.empresa_id()}/issues"), "issues")
        itens.sort(key=lambda i: i.get("updatedAt") or "", reverse=True)
        for it in itens[:40]:
            t = {"id": it.get("id"), "numero": it.get("identifier"), "titulo": it.get("title"), "estado": it.get("status"),
                 "criada_em": it.get("createdAt"), "atualizada_em": it.get("updatedAt"),
                 "origem": (re.search(r"\[origem:\s*([^\]]+)\]", it.get("description") or "") or [None, ""])[1] if "[origem:" in (it.get("description") or "") else "",
                 "entrega": "", "pergunta": "", "pendencia_id": ""}
            if it.get("status") in ("done", "cancelled", "blocked", "in_progress"):
                cs = esc.lista(esc.api("GET", f"/issues/{it['id']}/comments"), "comments")
                cs.sort(key=lambda c: c.get("createdAt") or "")
                for c in reversed(cs):
                    b = (c.get("body") or "").strip()
                    if b.upper().startswith(("ENTREGA:", "PENDÊNCIA:", "RECUSADO")):
                        t["entrega"] = b[:600]
                        break
            if it.get("status") in ("blocked", "in_progress", "todo"):
                for p_ in esc.pendencias_de(it["id"]):
                    t["pergunta"] = ((p_.get("payload") or {}).get("prompt") or "")[:600]
                    t["pendencia_id"] = p_.get("id", "")
                    break
            out_["tarefas"].append(t)
    except RuntimeError as e:
        out_["erro"] = str(e)[:200]
    return out_


def cmd_escritorio_set(home: Path) -> int:
    raw = stdin_value()
    try:
        dados = json.loads(raw or "{}")
    except ValueError:
        return out({"ok": False, "erro": "dados inválidos"})
    ligado = bool(dados.get("ligado"))
    (home / "escritorio.json").write_text(json.dumps({"ligado": ligado}, ensure_ascii=False) + "\n", encoding="utf-8")
    return out({"ok": True, "ligado": ligado})


def cmd_escritorio_decidir(home: Path, tarefa: str, aprovar: bool) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import jarvis_escritorio_mcp as esc
    except ImportError:
        return out({"ok": False, "erro": "ferramentas do Escritório ausentes"})
    nota = stdin_value()
    try:
        r = esc.aprovar(tarefa, nota) if aprovar else esc.recusar(tarefa, nota)
    except RuntimeError as e:
        return out({"ok": False, "erro": str(e)[:200]})
    return out(r)


def cmd_status(home: Path) -> int:
    limpar_esperas_velhas(home)
    profile = read_env(home / "profile.env")
    secrets_path = home / "secrets.env"
    filled = {k: bool(v) for k, v in read_env(secrets_path).items()}
    roles = [r for r in profile.get("JARVIS_ROLE", "").split(",") if r]
    declared = declared_secrets(home)
    if not declared:
        # instalação de um kernel sem secrets.json: só os nomes do secrets.env
        declared = [{"nome": k} for k in filled]
    segredos = []
    for s in declared:
        nome = s.get("nome", "")
        if not nome or nome in HIDDEN or s.get("gerado"):
            continue
        segredos.append({
            "nome": nome,
            "titulo": s.get("titulo", ""),
            "descricao": s.get("descricao", ""),
            "obter": s.get("obter", ""),
            "obrigatorio": bool(s.get("obrigatorio")),
            "implicito": bool(s.get("implicito")),
            "assistente": s.get("assistente") or "",
            "preenchido": filled.get(nome, False),
        })
    segredos.sort(key=lambda s: (not s["obrigatorio"], s["preenchido"], s["nome"]))
    bot = ""
    try:
        bot = (home / "telegram.bot").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return out({
        "ok": True,
        "tenant": profile.get("JARVIS_TENANT", ""),
        "papeis": roles,
        "dados": profile.get("JARVIS_DATA", ""),
        "vault": profile.get("VAULT_PATH", ""),
        "telegram": {
            "disponivel": "core" in roles,
            "token": filled.get("TELEGRAM_BOT_TOKEN", False),
            "chat": filled.get("TELEGRAM_HOME_CHANNEL", False),
            "recusado": (home / "telegram.decisao").is_file(),
            "bot": bot,
        },
        "segredos": segredos,
        "assistentes": assistentes_status(home, filled),
        "escritorio": escritorio_status(home, roles),
        "concluido": (home / "setup.done").is_file(),
        "pendente": pending_apply(home),
    })


# ---------------------------------------------------------------- set-secret
def cmd_set_secret(home: Path, name: str) -> int:
    if not NAME_RE.match(name or ""):
        return out({"ok": False, "erro": "nome de segredo inválido"})
    declared = declared_secrets(home)
    known = {s.get("nome") for s in declared} | set(read_env(home / "secrets.env"))
    generated = {s.get("nome") for s in declared if s.get("gerado")}
    # os gerados pelo kernel (senha do banco, tokens de bootstrap) não são da pessoa: trocá-los quebraria
    # o que já foi criado com eles
    if name not in known or name in HIDDEN or name in generated:
        return out({"ok": False, "erro": f"{name} não é um segredo deste tenant"})
    value = stdin_value()
    if "\n" in value:
        return out({"ok": False, "erro": "o valor tem mais de uma linha"})
    set_secret(home / "secrets.env", name, value)
    return out({"ok": True, "nome": name, "preenchido": bool(value)})


# ---------------------------------------------------------------- telegram
class TelegramError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(msg)
        self.code = code


class TelegramConnection(http.client.HTTPSConnection):
    """HTTPS que tenta IPv4 antes de IPv6, com conexão curta por endereço.

    Em muitas redes domésticas o IPv6 é anunciado mas não chega à internet: o Python tentaria o
    endereço IPv6 primeiro e só desistiria no fim do timeout inteiro (35 s parado antes de responder).
    Depois de conectado, o timeout volta ao valor pedido (o long-poll do getUpdates precisa dele).
    """

    CONNECT_TIMEOUT = 6

    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda i: i[0] != socket.AF_INET)
        err: OSError | None = None
        sock = None
        for fam, typ, proto, _, addr in infos:
            s = socket.socket(fam, typ, proto)
            s.settimeout(self.CONNECT_TIMEOUT)
            try:
                s.connect(addr)
                sock = s
                break
            except OSError as e:
                err = e
                s.close()
        if sock is None:
            raise err or OSError(f"sem rota para {self.host}")
        sock.settimeout(self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def tg_api(token: str, method: str, http_timeout: int = 40, **q):
    """Chama a API do bot; `q` vira query string (o `timeout` dela é o long-poll do Telegram)."""
    path = f"/bot{token}/{method}"
    if q:
        path += "?" + urllib.parse.urlencode(q)
    conn = TelegramConnection("api.telegram.org", timeout=http_timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise TelegramError(resp.status, f"HTTP {resp.status}")
        return json.loads(body)["result"]
    except TelegramError:
        raise
    except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
        raise TelegramError(None, str(e)) from e
    finally:
        conn.close()


def cmd_telegram_token(home: Path) -> int:
    token = stdin_value()
    if not token:
        return out({"ok": False, "erro": "token vazio"})
    try:
        me = tg_api(token, "getMe", http_timeout=20)
    except TelegramError as e:
        if e.code in (401, 404):
            return out({"ok": False, "erro": "o Telegram recusou o token (confira em @BotFather)"})
        return out({"ok": False, "erro": "não consegui falar com o Telegram; confira a internet e tente de novo"})
    username = str(me.get("username") or "")
    set_secret(home / "secrets.env", "TELEGRAM_BOT_TOKEN", token)
    (home / "telegram.bot").write_text(username + "\n", encoding="utf-8")
    try:
        (home / "telegram.decisao").unlink()
    except OSError:
        pass
    return out({"ok": True, "bot": username})


def hermes_bin() -> str | None:
    return shutil.which("hermes") or shutil.which("hermes", path=str(Path.home() / ".local" / "bin"))


def hermes_gateway(action: str) -> None:
    h = hermes_bin()
    if not h:
        return
    try:
        subprocess.run([h, "gateway", action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_telegram_wait(home: Path, seconds: int, since: int) -> int:
    token = read_env(home / "secrets.env").get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return out({"ok": False, "erro": "sem token do bot"})
    seconds = max(5, min(seconds, 300))
    deadline = time.time() + seconds
    offset = 0
    stopped_gateway = False
    try:
        # o backlog pode ter mensagem de qualquer pessoa: só vale o que chegar depois de `since`
        last = tg_api(token, "getUpdates", offset=-1, timeout=0)
        if last:
            offset = last[-1]["update_id"] + 1
            # a última pode ser a própria mensagem do dono, mandada antes de este comando começar
            m = last[-1].get("message") or {}
            if m.get("date", 0) >= since and (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    except TelegramError as e:
        if e.code != 409:
            return out({"ok": False, "erro": "não consegui falar com o Telegram"})
    while time.time() < deadline:
        try:
            ups = tg_api(token, "getUpdates", http_timeout=35, offset=offset, allowed_updates='["message"]',
                         timeout=min(25, max(1, int(deadline - time.time()))))
        except TelegramError as e:
            if e.code == 409 and not stopped_gateway:
                # outro leitor (o gateway do Hermes com este token) disputa as mensagens: para e devolve no fim
                hermes_gateway("stop")
                stopped_gateway = True
                time.sleep(2)
                continue
            if e.code == 409:
                time.sleep(2)
                continue
            if stopped_gateway:
                hermes_gateway("start")
            return out({"ok": False, "erro": "o Telegram não entregou mensagens; tente de novo"})
        for u in ups:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("date", 0) < since:
                continue
            if (m.get("chat") or {}).get("type") == "private" and (m.get("from") or {}).get("id"):
                return finish_wait(home, token, offset, m, stopped_gateway)
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": False, "erro": "nenhuma mensagem chegou ao bot", "tempo_esgotado": True})


def finish_wait(home: Path, token: str, offset: int, m: dict, stopped_gateway: bool) -> int:
    try:
        tg_api(token, "getUpdates", offset=offset, timeout=0)  # marca como lida: o gateway não responde a ela de novo
    except TelegramError:
        pass
    sender = m.get("from") or {}
    set_secret(home / "secrets.env", "TELEGRAM_HOME_CHANNEL", str(sender["id"]))
    if stopped_gateway:
        hermes_gateway("start")
    return out({"ok": True, "chat": True, "nome": str(sender.get("first_name") or "")})


def cmd_telegram_off(home: Path) -> int:
    (home / "telegram.decisao").write_text("nao\n", encoding="utf-8")
    return out({"ok": True})


def cmd_done(home: Path) -> int:
    (home / "setup.done").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return out({"ok": True})


# ---------------------------------------------------------------- atualização
# O kernel instalado é um clone git (install.sh). Versão nova = a origem no GitHub tem commit que este clone
# não contém. O JARVIS Web instalado é o alvo do link app/jarvis-web (app/jarvis-web-<versão>), e a versão
# nova vem do versions.env desse commit. A rede é consultada no máximo uma vez por --max-age (update.json);
# a parte local é recalculada sempre, então depois de atualizar o aviso some sozinho.
DEFAULT_REPO = "https://github.com/whitebeardit/whitebeard-os.git"
GITHUB_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
RELEASE_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/releases/download/([^/]+)/")
TOKEN_RE = re.compile(r"token=[0-9a-f]{16,}")


def https_get(host: str, path: str, timeout: int = 15, accept: str = "*/*") -> bytes:
    conn = TelegramConnection(host, timeout=timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "whitebeard-os", "Accept": accept})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status} em {host}{path}")
        return body
    finally:
        conn.close()


def kernel_dir(home: Path) -> Path | None:
    try:
        d = Path((home / "kernel.path").read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return d if (d / "bootstrap.sh").is_file() else None


def git(kernel: Path, *args: str, timeout: int = 20) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    try:
        r = subprocess.run(["git", "-C", str(kernel), *args], capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_ok(kernel: Path, *args: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(kernel), *args], capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def local_versions(home: Path, kernel: Path) -> dict:
    web = ""
    try:
        name = os.path.basename(os.path.realpath(home / "app" / "jarvis-web"))
        web = name[len("jarvis-web-"):] if name.startswith("jarvis-web-") else ""
    except OSError:
        pass
    # o ramo acompanhado na origem (origin/main numa instalação); um ramo local sem origem compara com main
    up = git(kernel, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    branch = up.split("/", 1)[1] if up.startswith("origin/") and "/" in up else "main"
    return {"web": web, "kernel": git(kernel, "rev-parse", "HEAD"), "ramo": branch}


def remote_versions(kernel: Path, branch: str) -> dict:
    m = GITHUB_RE.match(git(kernel, "remote", "get-url", "origin") or DEFAULT_REPO)
    if not m:
        raise OSError("a origem do kernel não está no GitHub")
    owner, name = m.group(1), m.group(2)
    # sempre por HTTPS: o repositório é público e o serviço não tem agente SSH
    out_ = git(kernel, "ls-remote", f"https://github.com/{owner}/{name}.git", f"refs/heads/{branch}", timeout=25)
    if not out_:
        raise OSError("não consegui consultar o GitHub")
    sha = out_.split()[0]
    body = https_get("raw.githubusercontent.com", f"/{owner}/{name}/{sha}/kernel/versions.env").decode("utf-8", "replace")
    vals = {}
    for line in body.splitlines():
        if line.startswith("JARVIS_WEB_") and "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v.strip().strip("'\"")
    return {"kernel": sha, "web": vals.get("JARVIS_WEB_VERSION", ""), "web_url": vals.get("JARVIS_WEB_URL", "")}


def release_notes(web_url: str) -> str:
    """Notas da release do pacote novo (GitHub Releases, público). Só enfeite: qualquer falha vira ''."""
    m = RELEASE_RE.match(web_url or "")
    if not m:
        return ""
    try:
        body = https_get("api.github.com", f"/repos/{m.group(1)}/{m.group(2)}/releases/tags/{m.group(3)}",
                         timeout=10, accept="application/vnd.github+json")
        notas = str(json.loads(body).get("body") or "").strip()
    except (OSError, ValueError):
        return ""
    return notas[:600]


def cmd_update_check(home: Path, max_age: int, force: bool) -> int:
    kernel = kernel_dir(home)
    if not kernel:
        return out({"ok": False, "erro": "kernel não encontrado (kernel.path)"})
    local = local_versions(home, kernel)
    cache_path = home / "update.json"
    cached: dict = {}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    now = int(time.time())
    age = now - int(cached.get("verificado") or 0)
    # uma falha de rede vale por menos tempo que uma resposta: a próxima abertura tenta de novo
    fresh = bool(cached) and age < (min(max_age, 3600) if cached.get("erro") else max_age)
    consultou = force or not fresh
    if consultou:
        try:
            remoto = remote_versions(kernel, local["ramo"])
            notas = release_notes(remoto["web_url"]) if remoto["web"] and remoto["web"] != local["web"] else ""
            cached = {"verificado": now, "remoto": remoto, "notas": notas}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            cached = {"verificado": now, "remoto": cached.get("remoto"), "notas": cached.get("notas", ""),
                      "erro": "não consegui consultar as versões novas (sem internet?)", "detalhe": str(e)[:160]}
        try:
            cache_path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
        except OSError:
            pass
    remoto = cached.get("remoto") or {}
    sha = remoto.get("kernel", "")
    # novo = a origem tem um commit que este clone não contém (um clone adiantado, de quem desenvolve, não conta)
    kernel_novo = bool(sha) and sha != local["kernel"] and not (
        git_ok(kernel, "cat-file", "-e", f"{sha}^{{commit}}") and git_ok(kernel, "merge-base", "--is-ancestor", sha, "HEAD"))
    # o pacote do JARVIS Web também conta: o clone pode estar em dia sem o bootstrap ter rodado depois
    web_novo = bool(remoto.get("web")) and remoto["web"] != local["web"]
    disponivel = kernel_novo or web_novo
    return out({
        "ok": True,
        "disponivel": disponivel,
        "local": {"web": local["web"], "kernel": local["kernel"]},
        "remoto": {"web": remoto.get("web", ""), "kernel": sha},
        "notas": cached.get("notas", "") if disponivel else "",
        "verificado": cached.get("verificado"),
        "erro": cached.get("erro"),
        "consultou": consultou,
    })


def update_paths(home: Path) -> tuple[Path, Path, Path]:
    return home / "update.run.json", home / "update.exit", home / "logs" / "update.log"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def run_state(home: Path) -> dict:
    run_path, exit_path, log_path = update_paths(home)
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"existe": False, "rodando": False, "codigo": None}
    codigo = None
    try:
        codigo = int(exit_path.read_text(encoding="utf-8").strip() or -1)
    except (OSError, ValueError):
        pass
    if codigo is not None:
        rodando = False
    elif run.get("unidade"):
        try:
            rodando = subprocess.run(["systemctl", "--user", "is-active", "--quiet", str(run["unidade"])],
                                     capture_output=True, timeout=10, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            rodando = False
    else:
        rodando = pid_alive(int(run.get("pid") or 0))
    if codigo is None and not rodando:
        codigo = -1  # parou sem escrever o código: morto no meio
    fim = None
    if codigo is not None:
        try:
            fim = int(exit_path.stat().st_mtime)
        except OSError:
            fim = int(time.time())
    return {"existe": True, "rodando": rodando, "codigo": codigo, "inicio": run.get("inicio"), "fim": fim, "log": str(log_path)}


def cmd_update_run(home: Path) -> int:
    kernel = kernel_dir(home)
    if not kernel:
        return out({"ok": False, "erro": "kernel não encontrado (kernel.path)"})
    install = kernel / "install.sh"
    if not install.is_file():
        return out({"ok": False, "erro": "install.sh não encontrado no kernel"})
    if run_state(home)["rodando"]:
        return out({"ok": False, "erro": "já há uma atualização em andamento"})
    run_path, exit_path, log_path = update_paths(home)
    profile = read_env(home / "profile.env")
    local = local_versions(home, kernel)
    try:
        tenant = (home / "tenant.path").read_text(encoding="utf-8").strip()
    except OSError:
        tenant = ""
    if not tenant:
        return out({"ok": False, "erro": "tenant.path ausente: não sei qual tenant reinstalar"})
    args = ["sh", str(install), "--no-prompt"]
    if profile.get("JARVIS_ROLE"):
        args += ["--role", profile["JARVIS_ROLE"]]
    wrapper = f"{shlex.join(args)} >{shlex.quote(str(log_path))} 2>&1; echo $? >{shlex.quote(str(exit_path))}"
    env_extra = {"JARVIS_TENANT": tenant, "WHITEBEARD_OS_DIR": str(kernel), "WHITEBEARD_OS_REF": local["ramo"],
                 "JARVIS_HOME": str(home), "HOME": str(Path.home())}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for p in (exit_path, home / "update.json"):
        try:
            p.unlink()
        except OSError:
            pass
    now = int(time.time())
    record: dict = {"inicio": now, "log": str(log_path), "de": local}
    # Linux: unidade própria do systemd (o restart do jarvis-web mata o cgroup inteiro, e o filho iria junto).
    # macOS e sem systemd: sessão nova (setsid); o launchd só derruba o grupo de processos do serviço.
    started = False
    if sys.platform.startswith("linux") and shutil.which("systemd-run"):
        unit = f"whitebeard-update-{now}"
        cmd = ["systemd-run", "--user", "--quiet", "--collect", "--unit", unit]
        cmd += [f"--setenv={k}={v}" for k, v in env_extra.items()]
        cmd += ["sh", "-c", wrapper]
        try:
            started = subprocess.run(cmd, capture_output=True, timeout=30, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            started = False
        if started:
            record["unidade"] = unit
    if not started:
        try:
            p = subprocess.Popen(["sh", "-c", wrapper], cwd=str(kernel), env={**os.environ, **env_extra},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError as e:
            return out({"ok": False, "erro": f"não consegui iniciar a atualização: {e}"})
        record["pid"] = p.pid
    run_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return out({"ok": True, "inicio": now})


def cmd_update_status(home: Path) -> int:
    st = run_state(home)
    linhas: list[str] = []
    if st["existe"]:
        try:
            raw = Path(st["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raw = []
        for line in raw[-15:]:
            line = TOKEN_RE.sub("token=…", line.replace("[whitebeard-os] ", "").replace("[whitebeard-os/install] ", "")).rstrip()
            if line:
                linhas.append(line)
    kernel = kernel_dir(home)
    local = local_versions(home, kernel) if kernel else {"web": "", "kernel": ""}
    return out({"ok": True, **st, "linhas": linhas, "local": {"web": local["web"], "kernel": local["kernel"]}})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("set-secret")
    p.add_argument("name")
    sub.add_parser("telegram-token")
    p = sub.add_parser("telegram-wait")
    p.add_argument("--seconds", type=int, default=170)
    p.add_argument("--since", type=int, default=0)
    sub.add_parser("telegram-off")
    sub.add_parser("done")
    p = sub.add_parser("update-check")
    p.add_argument("--max-age", type=int, default=86400)
    p.add_argument("--force", action="store_true")
    sub.add_parser("update-run")
    sub.add_parser("update-status")
    sub.add_parser("assistente-add")
    p = sub.add_parser("assistente-remove")
    p.add_argument("nome")
    p = sub.add_parser("assistente-update")
    p.add_argument("nome")
    p = sub.add_parser("assistente-token")
    p.add_argument("nome")
    p = sub.add_parser("assistente-wait")
    p.add_argument("nome")
    p.add_argument("--seconds", type=int, default=170)
    p.add_argument("--since", type=int, default=0)
    for c in ("assistente-whatsapp-parear", "assistente-whatsapp-status", "assistente-whatsapp-desparear"):
        p = sub.add_parser(c)
        p.add_argument("nome")
    sub.add_parser("escritorio-set")
    for c in ("escritorio-aprovar", "escritorio-recusar"):
        p = sub.add_parser(c)
        p.add_argument("tarefa")
    args = ap.parse_args()
    home = Path(args.home).expanduser()
    if not (home / "profile.env").is_file():
        return out({"ok": False, "erro": f"instalação não encontrada em {home}"})
    if args.cmd == "status":
        return cmd_status(home)
    if args.cmd == "set-secret":
        return cmd_set_secret(home, args.name)
    if args.cmd == "telegram-token":
        return cmd_telegram_token(home)
    if args.cmd == "telegram-wait":
        return cmd_telegram_wait(home, args.seconds, args.since or int(time.time()))
    if args.cmd == "telegram-off":
        return cmd_telegram_off(home)
    if args.cmd == "done":
        return cmd_done(home)
    if args.cmd == "update-check":
        return cmd_update_check(home, max(0, args.max_age), args.force)
    if args.cmd == "update-run":
        return cmd_update_run(home)
    if args.cmd == "update-status":
        return cmd_update_status(home)
    if args.cmd == "assistente-add":
        return cmd_assistente_add(home)
    if args.cmd == "assistente-remove":
        return cmd_assistente_remove(home, args.nome)
    if args.cmd == "assistente-update":
        return cmd_assistente_update(home, args.nome)
    if args.cmd == "assistente-token":
        return cmd_assistente_token(home, args.nome)
    if args.cmd == "assistente-wait":
        return cmd_assistente_wait(home, args.nome, args.seconds, args.since or int(time.time()))
    if args.cmd == "assistente-whatsapp-parear":
        return cmd_assistente_whatsapp_parear(home, args.nome)
    if args.cmd == "assistente-whatsapp-status":
        return cmd_assistente_whatsapp_status(home, args.nome)
    if args.cmd == "assistente-whatsapp-desparear":
        return cmd_assistente_whatsapp_desparear(home, args.nome)
    if args.cmd == "escritorio-set":
        return cmd_escritorio_set(home)
    if args.cmd == "escritorio-aprovar":
        return cmd_escritorio_decidir(home, args.tarefa, True)
    if args.cmd == "escritorio-recusar":
        return cmd_escritorio_decidir(home, args.tarefa, False)
    return 2


if __name__ == "__main__":
    sys.exit(main())
