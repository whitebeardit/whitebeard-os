#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6,<7", "jsonschema>=4,<5"]
# ///
"""Renderiza a instalação de um tenant do Whitebeard OS a partir do jarvis.yaml.

Entrada: jarvis.yaml (declaração do tenant), manifesto de segredos e lockfile do kernel, papéis do nó.
Saída em $JARVIS_HOME: jarvis.yaml (cópia), secrets.env (modo 600), profile.sh, skills/ (copiadas e
linkadas em ~/.claude/skills) e, em render/: claude-mcp.sh (desktop), knowledge.env (knowledge),
hermes/config.yaml e hermes/env (core), summary.txt. Nunca imprime valor de segredo.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets as pysecrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROLES = ("desktop", "core", "satelite", "knowledge")
MODULOS_PADRAO = ["sistemas", "conhecimento", "arquivos", "comunicacoes", "ferramentas", "diagnosticos", "camera", "operacao"]
VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def log(msg: str) -> None:
    print(f"[render] {msg}")


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def sq(text: str) -> str:
    """Aspas simples para sh (sem expansão)."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


def dq(text: str) -> str:
    """Aspas duplas para sh (mantém expansão de ${VAR})."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Writer:
    def __init__(self, dry: bool) -> None:
        self.dry = dry

    def write(self, path: Path, content: str, mode: int = 0o644) -> None:
        if self.dry:
            log(f"escreveria {path} ({len(content.splitlines())} linhas, modo {oct(mode)[2:]})")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)


# ---------------------------------------------------------------- assistentes do Telegram
# Ferramentas do Hermes que cada tipo tem no Telegram (e no chat local). O que não está na lista não existe
# para aquele assistente: o limite é estrutural, não de prompt. `mim` não restringe (é o assistente do dono).
FERRAMENTAS_BASE = ["clarify", "memory", "session_search", "skills", "vision"]
FERRAMENTAS_TIPO = {
    "mim": None,
    "equipe": FERRAMENTAS_BASE + ["todo", "web"],
    "cliente": FERRAMENTAS_BASE,
    "rotinas": FERRAMENTAS_BASE + ["todo", "web"],
}
FERRAMENTAS_COMPUTADOR = ["terminal", "file", "code_execution"]
TIPO_NOME = {"mim": "para mim", "equipe": "para a equipe", "cliente": "para um cliente", "rotinas": "só rotinas"}

# Onde o assistente fala. Cada canal tem o prefixo das variáveis do Hermes e o nome dos três segredos por
# assistente (token, quem fala com ele, grupos/canais onde responde). `telegram` é o padrão: um tenant sem
# `canal` rende exatamente como antes. Novos canais entram aqui (whatsapp e slack: próximas fases).
CANAIS = {
    "telegram": {"nome": "Telegram", "token": "TELEGRAM_BOT_TOKEN", "pessoas": "TELEGRAM_ALLOWED_USERS", "grupos": "TELEGRAM_GROUP_ALLOWED_CHATS",
                 "obter_token": "@BotFather no Telegram: /newbot, cole o token", "grupos_rotulo": "Grupos",
                 "grupos_desc": "Identificações dos grupos onde ele responde (números negativos, separados por vírgula).",
                 "obter_grupos": "adicione o bot ao grupo; a página ASSISTENTES reconhece o grupo"},
    "discord": {"nome": "Discord", "token": "DISCORD_BOT_TOKEN", "pessoas": "DISCORD_ALLOWED_USERS", "grupos": "DISCORD_ALLOWED_CHANNELS",
                "obter_token": "Discord Developer Portal → sua aplicação → Bot → Reset Token", "grupos_rotulo": "Canais",
                "grupos_desc": "Identificações dos canais do Discord onde ele responde (números, separados por vírgula).",
                "obter_grupos": "mencione o bot no canal; a página ASSISTENTES reconhece o canal"},
    # WhatsApp (ponte Baileys do Hermes, não oficial): sem token; a sessão pareada por QR fica em
    # $JARVIS_HOME/assistentes/<nome>/whatsapp/session (creds.json) e é ela que vale como "token"
    "whatsapp": {"nome": "WhatsApp", "token": None, "pessoas": "WHATSAPP_ALLOWED_USERS", "grupos": "WHATSAPP_GROUP_ALLOWED_USERS",
                 "obter_token": "leia o QR na página ASSISTENTES", "grupos_rotulo": "Grupos",
                 "grupos_desc": "Identificações dos grupos do WhatsApp onde ele responde (terminam em @g.us, separadas por vírgula).",
                 "obter_grupos": "mande uma mensagem no grupo enquanto a página ASSISTENTES espera"},
}


def escritorio_ligado(home: Path, data: dict) -> bool:
    """A página liga/desliga em $JARVIS_HOME/escritorio.json (vale por cima do tenant, como os assistentes)."""
    try:
        loja = json.loads((home / "escritorio.json").read_text(encoding="utf-8"))
        if isinstance(loja, dict) and "ligado" in loja:
            return bool(loja["ligado"])
    except (OSError, ValueError):
        pass
    return bool((data.get("escritorio") or {}).get("ligado"))


CARGO_NOME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
CARGO_PADRAO = {"nome": "trabalho", "titulo": "Assistente de trabalho",
                "faz": "Executa o que o JARVIS e os assistentes delegam: pesquisas, documentos, propostas, resumos."}
ROTINA_PADRAO = {"nome": "resumo-da-semana", "titulo": "Resumo da semana", "quando": "0 8 * * 1", "onde": "telegram", "cargo": "trabalho", "ligada": True,
                 "faz": ("Faça o resumo da semana do Escritório e do second brain: o que foi entregue, o que está pendente ou parado, "
                         "e o que mudou nas páginas do second brain nos últimos sete dias. Em tópicos curtos, em português do Brasil.")}


def catalogo_escritorio(home: Path, data: dict) -> tuple[list[dict], list[dict]]:
    """Cargos e rotinas do Escritório: os do tenant com os da página por cima (escritorio.json: cargos; nome repetido
    vale o da página; `removido` esconde). O cargo `trabalho` e a rotina `resumo-da-semana` existem sempre. As rotinas
    da página ficam em escritorio/rotinas.json e não passam pelo render: o carteiro lê de lá direto."""
    esc = data.get("escritorio") or {}
    cargos: list[dict] = [dict(CARGO_PADRAO)]
    for c in esc.get("cargos") or []:
        if isinstance(c, dict) and CARGO_NOME_RE.match(str(c.get("nome") or "")):
            cargos = [x for x in cargos if x["nome"] != c["nome"]]
            cargos.append({"nome": c["nome"], "titulo": str(c.get("titulo") or c["nome"])[:80], "faz": str(c.get("faz") or "")[:600],
                           **({"nivel": c["nivel"]} if c.get("nivel") in ("rapido", "equilibrado", "capaz") else {})})
    try:
        loja = json.loads((home / "escritorio.json").read_text(encoding="utf-8"))
        loja_cargos = loja.get("cargos") if isinstance(loja, dict) else []
    except (OSError, ValueError):
        loja_cargos = []
    for c in loja_cargos or []:
        if not isinstance(c, dict) or not CARGO_NOME_RE.match(str(c.get("nome") or "")) or c["nome"] == "trabalho" and c.get("removido"):
            continue
        cargos = [x for x in cargos if x["nome"] != c["nome"]]
        if c.get("removido"):
            continue
        cargos.append({"nome": c["nome"], "titulo": str(c.get("titulo") or c["nome"])[:80], "faz": str(c.get("faz") or "")[:600],
                       **({"nivel": c["nivel"]} if c.get("nivel") in ("rapido", "equilibrado", "capaz") else {})})
    if not any(c["nome"] == "trabalho" for c in cargos):
        cargos.insert(0, dict(CARGO_PADRAO))
    rotinas: list[dict] = [dict(ROTINA_PADRAO)]
    for r in esc.get("rotinas") or []:
        if isinstance(r, dict) and CARGO_NOME_RE.match(str(r.get("nome") or "")):
            rotinas = [x for x in rotinas if x["nome"] != r["nome"]]
            rotinas.append({"nome": r["nome"], "titulo": str(r.get("titulo") or r["nome"])[:120], "faz": str(r.get("faz") or "")[:2000],
                            "quando": str(r.get("quando") or ""), "onde": str(r.get("onde") or "telegram"), "cargo": str(r.get("cargo") or "trabalho"),
                            "ligada": bool(r.get("ligada", True))})
    return cargos, rotinas


_QP: dict = {}
PROVEDORES_PAGINA = ("claude", "chatgpt", "openrouter")  # os de kernel/modelos.yaml


def quem_pensa(home: Path, kernel: Path, data: dict, forcar: bool = False) -> dict | None:
    """`forcar`: um assistente escolheu a própria assinatura; sem escolha geral, vale o padrão da tabela."""
    chave = f"{home}|{forcar}"
    if chave not in _QP:
        _QP[chave] = _quem_pensa(home, kernel, data, forcar)
    return _QP[chave]


def _quem_pensa(home: Path, kernel: Path, data: dict, forcar: bool = False) -> dict | None:
    """Provedor e nível escolhidos (página em $JARVIS_HOME/pensa.json por cima de hermes.provedor/nivel do tenant).
    None quando ninguém escolheu: vale o hermes.provider/model do tenant, como antes."""
    try:
        tabela = yaml.safe_load((kernel / "modelos.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    hcfg = data.get("hermes") or {}
    try:
        loja = json.loads((home / "pensa.json").read_text(encoding="utf-8"))
        loja = loja if isinstance(loja, dict) else {}
    except (OSError, ValueError):
        loja = {}
    provedor = loja.get("provedor") or hcfg.get("provedor")
    nivel = loja.get("nivel") or hcfg.get("nivel")
    if not provedor and not nivel and not forcar:
        return None
    provs = tabela.get("provedores") or {}
    padrao = tabela.get("padrao") or {}
    provedor = provedor if provedor in provs else padrao.get("provedor", "claude")
    nivel = nivel if nivel in (tabela.get("niveis") or {}) else padrao.get("nivel", "equilibrado")
    logados = [x for x in (loja.get("logados") or []) if x in provs]
    # escolha da página sem a conta conectada (saiu, expirou): o JARVIS não pode ficar sem credencial; volta ao padrão
    if loja and provedor != padrao.get("provedor", "claude") and provedor not in logados:
        log(f"aviso: quem pensa: {provedor} escolhido sem conta conectada; usando {padrao.get('provedor', 'claude')}")
        provedor = padrao.get("provedor", "claude")
    reserva = next((x for x in provs if x != provedor and x in logados), None)
    return {"tabela": provs, "provedor": provedor, "nivel": nivel, "reserva": reserva, "da_pagina": bool(loja),
            "logados": logados, "padrao": padrao.get("provedor", "claude")}


def modelo_resolvido(qp: dict | None, hermes_cfg: dict, nivel: str | None = None, modelo: str | None = None,
                     provedor: str | None = None, quem: str = "") -> tuple[dict, dict | None]:
    """(model, fallback_model) no formato do config do Hermes para um perfil. `provedor` e `nivel` do assistente/cargo
    vencem os gerais; `modelo` explícito (técnico, no yaml) vence tudo. Sem escolha (qp None), o comportamento antigo.
    A assinatura do assistente só vale com a conta conectada (o padrão, Claude, sempre vale); senão, a geral."""
    if qp is None:
        model = {"provider": hermes_cfg.get("provider", "anthropic"), "default": modelo or hermes_cfg.get("model", "")}
        return model, None
    if provedor and provedor in qp["tabela"] and provedor != qp["provedor"]:
        if provedor == qp.get("padrao") or provedor in (qp.get("logados") or []):
            outros = [x for x in qp["tabela"] if x != provedor and (x in (qp.get("logados") or []) or x == qp.get("padrao"))]
            qp = {**qp, "provedor": provedor, "reserva": outros[0] if outros else None}
        else:
            log(f"aviso: {quem or 'assistente'}: {qp['tabela'][provedor]['nome']} escolhido sem conta conectada; usando a assinatura geral")
    prov = qp["tabela"][qp["provedor"]]
    niv = nivel if nivel in prov["niveis"] else qp["nivel"]
    model = {"provider": prov["hermes"], "default": modelo or prov["niveis"][niv]}
    if qp["reserva"]:
        outro = qp["tabela"][qp["reserva"]]
        fb = {"provider": outro["hermes"], "model": outro["niveis"][niv]}
    else:
        # sem a outra assinatura: o nível mais rápido do mesmo provedor (quando o principal já não é ele)
        rapido = prov["niveis"].get("rapido")
        fb = {"provider": prov["hermes"], "model": rapido} if rapido and rapido != model["default"] else None
    return model, fb


def hermes_home_de(home: Path) -> Path:
    return Path(parse_env(home / "profile.env").get("HERMES_HOME") or str(Path.home() / ".hermes"))


def whatsapp_sessao(home: Path, nome: str) -> Path:
    """A sessão fica numa pasta do kernel (não no perfil do Hermes, que só existe depois de colocar em uso);
    o config.yaml do perfil aponta para ela (whatsapp.session_path)."""
    return home / "assistentes" / nome / "whatsapp" / "session"


def whatsapp_telefone(sessao: Path) -> str:
    """Número da conta pareada (creds.json → me.id, no formato número:dispositivo + sufixo do WhatsApp)."""
    try:
        me = json.loads((sessao / "creds.json").read_text(encoding="utf-8")).get("me") or {}
        return str(me.get("id", "")).split(":", 1)[0].split("@", 1)[0]
    except (OSError, ValueError, AttributeError):
        return ""


def canal_de(a: dict) -> str:
    return a.get("canal") or "telegram"


def chaves_assistente(a: dict) -> dict[str, str]:
    """Nomes dos segredos deste assistente em secrets.env: <VAR do canal>_<NOME>."""
    up = a["nome"].upper().replace("-", "_")
    c = CANAIS[canal_de(a)]
    return {"token": f"{c['token']}_{up}" if c["token"] else "", "pessoas": f"{c['pessoas']}_{up}", "grupos": f"{c['grupos']}_{up}"}


def installed_skills_hint(kernel: Path, tenant_dir: Path, data: dict) -> set[str]:
    """Nomes das skills que o render instala (kernel + packs do tenant), para validar `assistentes[].skills`."""
    names: set[str] = set()
    for pack in [kernel / "skills"] + [(tenant_dir / p).resolve() for p in (data.get("skills") or {}).get("packs", []) or []]:
        if pack.is_dir():
            names.update(d.name for d in pack.iterdir() if (d / "SKILL.md").is_file())
    return names


def soul_assistente(a: dict, tenant: str) -> str:
    tipo = a["tipo"]
    linhas = [f"Você é {a['nome']}, um assistente do JARVIS ({TIPO_NOME[tipo]}) do tenant {tenant}.", ""]
    if a.get("descricao"):
        linhas += ["O que você faz:", a["descricao"], ""]
    linhas += ["Regras que não mudam:"]
    if tipo == "cliente":
        linhas += [
            "- Você atende só o grupo em que foi colocado e só sobre o papel descrito acima. Fora disso, diga que não pode ajudar com isso.",
            "- Você não tem acesso a arquivos, comandos, outros sistemas nem a outras conversas. Se pedirem, diga que isso fica com a equipe.",
            "- Nunca repita dados de outras pessoas ou de outros clientes. Nunca invente o que não sabe.",
        ]
    elif tipo == "equipe":
        linhas += [
            "- Você ajuda a equipe com o papel descrito acima, usando as skills e o conhecimento que recebeu.",
            "- Você não executa nada neste computador" + (" (exceto o que o dono liberou)." if (a.get("ajustes") or {}).get("computador") else "."),
            "- O que você anota no second brain e no Paperclip fica marcado como seu.",
        ]
    elif tipo == "rotinas":
        linhas += ["- Você não conversa: executa rotinas agendadas e publica o resultado no canal combinado."]
    else:
        linhas += ["- Você é o assistente do dono deste computador e age em nome dele."]
    linhas += ["- Responda em português do Brasil, curto e direto, sem jargão.", ""]
    return "\n".join(linhas)


def limpar_assistentes_antigos(w: "Writer", home: Path, atuais: list[str]) -> None:
    """Pastas renderizadas de assistentes que saíram do tenant: apagadas, senão o bootstrap os reativaria."""
    base = home / "render" / "hermes" / "assistentes"
    if w.dry or not base.is_dir():
        return
    for d in base.iterdir():
        if d.is_dir() and d.name not in atuais:
            shutil.rmtree(d)


def render_assistentes(w: "Writer", home: Path, data: dict, assistentes: list[dict], values: dict[str, str],
                       tenant: str, vault_path: str, hermes_mcp: dict, skills_ok: set[str]) -> int:
    hermes_cfg = data.get("hermes") or {}
    nomes: list[str] = []
    catalogo: list[dict] = []
    for a in assistentes:
        nome, tipo = a["nome"], a["tipo"]
        if nome in nomes:
            print(f"[render] assistentes: nome repetido {nome}", file=sys.stderr)
            return 2
        nomes.append(nome)
        ajustes = a.get("ajustes") or {}
        if tipo in ("cliente", "rotinas") and any(ajustes.values()):
            print(f"[render] assistentes: {nome} é do tipo {tipo}; ajustes (computador, anotar) são proibidos nesse tipo", file=sys.stderr)
            return 2
        faltam = [s for s in a.get("skills") or [] if s not in skills_ok]
        if faltam:
            print(f"[render] assistentes: {nome} pede skills que não existem: {', '.join(faltam)}", file=sys.stderr)
            return 2
        canal = canal_de(a)
        if canal not in CANAIS:
            print(f"[render] assistentes: {nome} tem canal desconhecido {canal}", file=sys.stderr)
            return 2
        chaves = chaves_assistente(a)
        sessao = whatsapp_sessao(home, nome) if canal == "whatsapp" else None
        token = ("pareado" if (sessao / "creds.json").is_file() else "") if sessao else values.get(chaves["token"], "")
        # no Telegram o dono entra sozinho (TELEGRAM_HOME_CHANNEL); nos outros canais não há id do dono: ele
        # é reconhecido pelo "oi" como qualquer pessoa
        dono = str(values.get("TELEGRAM_HOME_CHANNEL", "")).strip() if canal == "telegram" else ""
        pessoas = [p.strip() for p in str(values.get(chaves["pessoas"], "")).split(",") if p.strip()]
        if tipo in ("mim", "equipe") and dono.isdigit() and dono not in pessoas:
            pessoas.insert(0, dono)
        grupos = [g.strip() for g in str(values.get(chaves["grupos"], "")).split(",") if g.strip()]
        # ferramentas por plataforma: lista fechada por tipo (+ computador, só para equipe, com aviso)
        tools = FERRAMENTAS_TIPO[tipo]
        if tools is not None and tipo == "equipe" and ajustes.get("computador"):
            tools = tools + FERRAMENTAS_COMPUTADOR
        provedor_a = a.get("provedor") if a.get("provedor") in PROVEDORES_PAGINA else None
        model, fb = modelo_resolvido(quem_pensa(home, Path(__file__).resolve().parent.parent, data, forcar=bool(provedor_a)), hermes_cfg,
                                     a.get("nivel"), a.get("modelo"), provedor_a, nome)
        cfg: dict = {"model": model}
        if fb:
            cfg["fallback_model"] = fb
        if tools is not None:
            cfg["platform_toolsets"] = {canal: sorted(tools), "cli": sorted(tools)}
        # acesso: quem fala com ele. Cliente: só grupo, com menção; rotinas: ninguém
        # Conversa direta: quem não está na lista recebe UMA resposta educada dizendo onde o assistente atende,
        # em vez de silêncio (dm_policy "disabled" ignora tudo, inclusive /start; "allowlist" + "decline" responde
        # uma vez e some). A lista vazia vale como "ninguém".
        tg: dict = {"require_mention": tipo != "mim", "dm_policy": "allowlist", "unauthorized_dm_behavior": "decline"}
        rotulo_desc = (a.get("descricao") or "").strip()
        if tipo == "mim":
            tg["group_policy"] = "disabled"
            recusa = "Este assistente é pessoal: atende só o dono."
        elif tipo == "equipe":
            tg["group_policy"] = "allowlist" if grupos else "disabled"
            recusa = "Este assistente atende a equipe. Peça ao dono para liberar você na página ASSISTENTES do JARVIS."
        elif tipo == "cliente":
            tg["group_policy"] = "allowlist"
            recusa = "Eu atendo só no grupo combinado; fale comigo lá: escreva a pergunta com o meu @ no fim, ou responda a uma mensagem minha." + (f" O que eu faço: {rotulo_desc}" if rotulo_desc else "")
        else:
            tg["group_policy"] = "disabled"
            recusa = "Este assistente só executa rotinas; ele não conversa."
        tg["unauthorized_dm_decline_message"] = recusa
        cfg["unauthorized_dm_behavior"] = "decline"
        cfg["unauthorized_dm_decline_message"] = recusa
        # MCPs: o dono e a equipe veem os do tenant (o cérebro entre eles); cliente e rotinas, nenhum
        if tipo in ("mim", "equipe") and hermes_mcp:
            cfg["mcp_servers"] = hermes_mcp
        env = [f"{CANAIS[canal]['token']}={token}"] if CANAIS[canal]["token"] else []
        if canal == "telegram":
            cfg["telegram"] = tg
            env.append("TELEGRAM_ALLOWED_USERS=" + ",".join(pessoas))
            # grupos: TELEGRAM_ALLOWED_CHATS é a trava de onde ele responde; TELEGRAM_GROUP_ALLOWED_CHATS marca os
            # grupos autorizados; e qualquer membro desses grupos pode falar com ele (TELEGRAM_GROUP_ALLOWED_USERS=*),
            # senão o Hermes exige que cada pessoa esteja numa lista e o grupo fica mudo
            env.append("TELEGRAM_ALLOWED_CHATS=" + ",".join(grupos))
            env.append("TELEGRAM_GROUP_ALLOWED_CHATS=" + ",".join(grupos))
            env.append("TELEGRAM_GROUP_ALLOWED_USERS=" + ("*" if grupos else ""))
            env.append(f"TELEGRAM_REQUIRE_MENTION={'false' if tipo == 'mim' else 'true'}")
        elif canal == "discord":
            # Discord no Hermes: com DISCORD_ALLOWED_USERS preenchido, só essas pessoas falam com ele (em
            # conversa direta e nos canais); vazio, qualquer membro dos canais em DISCORD_ALLOWED_CHANNELS fala,
            # e ninguém em conversa direta. Então: cliente = só canais; mim = só pessoas; equipe = pessoas
            # reconhecidas (cada uma menciona o bot uma vez) nos canais reconhecidos.
            cfg["discord"] = {"require_mention": tipo != "mim"}
            env.append("DISCORD_ALLOWED_USERS=" + ",".join(pessoas if tipo in ("mim", "equipe") else []))
            env.append("DISCORD_ALLOWED_CHANNELS=" + ",".join(grupos if tipo in ("equipe", "cliente") else []))
            env.append(f"DISCORD_REQUIRE_MENTION={'false' if tipo == 'mim' else 'true'}")
            env.append("DISCORD_IGNORE_NO_MENTION=true")
        elif canal == "whatsapp":
            # modo bot (número dedicado) ou self-chat (o próprio número, a pessoa fala consigo mesma). As
            # políticas do Hermes: dm allowlist (pessoas) ou disabled; grupos allowlist (ids) ou disabled; a
            # ponte só sobe com WHATSAPP_ENABLED. Cada assistente tem a sua porta de ponte e a sua sessão.
            modo = a.get("whatsapp_modo") or "bot"
            if modo == "self-chat" and (tel := whatsapp_telefone(sessao)) and tel not in pessoas:
                pessoas.insert(0, tel)
            wa_pessoas = pessoas if tipo in ("mim", "equipe") else []
            wa_grupos = grupos if tipo in ("equipe", "cliente") and modo == "bot" else []
            cfg["whatsapp"] = {"bridge_port": 3100 + sorted(x["nome"] for x in assistentes if canal_de(x) == "whatsapp").index(nome),
                               "session_path": str(sessao), "dm_policy": "allowlist" if wa_pessoas else "disabled",
                               "group_policy": "allowlist" if wa_grupos else "disabled", "require_mention": tipo != "mim",
                               "unauthorized_dm_behavior": "ignore"}
            env.append("WHATSAPP_ENABLED=true" if token else "WHATSAPP_ENABLED=false")
            env.append(f"WHATSAPP_MODE={modo}")
            env.append("WHATSAPP_ALLOWED_USERS=" + ",".join(wa_pessoas))
            env.append("WHATSAPP_GROUP_ALLOWED_USERS=" + ",".join(wa_grupos))
            env.append("WHATSAPP_DM_POLICY=" + ("allowlist" if wa_pessoas else "disabled"))
            env.append("WHATSAPP_GROUP_POLICY=" + ("allowlist" if wa_grupos else "disabled"))
            env.append(f"WHATSAPP_REQUIRE_MENTION={'false' if tipo == 'mim' else 'true'}")
        env += [f"VAULT_PATH={vault_path}", f"JARVIS_TENANT={tenant}", f"WHITEBEARD_ASSISTENTE={nome}", f"WHITEBEARD_ASSISTENTE_TIPO={tipo}"]
        if tipo in ("mim", "equipe"):
            for k in ("GBRAIN_MCP_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if values.get(k):
                    env.append(f"{k}={values[k]}")
        base = home / "render" / "hermes" / "assistentes" / nome
        w.write(base / "config.yaml", "# gerado pelo whitebeard-os para o assistente " + nome + "\n" + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        w.write(base / "env", "# gerado pelo whitebeard-os; variáveis do perfil do Hermes deste assistente\n" + "\n".join(env) + "\n", mode=0o600)
        w.write(base / "SOUL.md", soul_assistente(a, tenant))
        w.write(base / "skills.txt", "\n".join(a.get("skills") or []) + ("\n" if a.get("skills") else ""))
        catalogo.append({
            "nome": nome, "tipo": tipo, "canal": canal, "descricao": a.get("descricao", ""), "skills": list(a.get("skills") or []),
            "do_pacote": bool(a.get("do_pacote")),
            "ajustes": {"computador": bool(ajustes.get("computador")), "anotar": bool(ajustes.get("anotar"))},
            "segredos": {"token": chaves["token"] or None,
                         "pessoas": chaves["pessoas"] if tipo in ("mim", "equipe") else None,
                         "grupos": chaves["grupos"] if tipo in ("equipe", "cliente") else None},
            **({"whatsapp": {"modo": a.get("whatsapp_modo") or "bot", "sessao": str(sessao), "telefone": whatsapp_telefone(sessao)}} if sessao else {}),
            "pronto": bool(token) and (tipo != "cliente" or bool(grupos)),
        })
    w.write(home / "render" / "hermes" / "assistentes.json", json.dumps(catalogo, ensure_ascii=False, indent=2) + "\n")
    limpar_assistentes_antigos(w, home, nomes)
    log(f"assistentes ({len(catalogo)}): " + ", ".join(
        f"{c['nome']} ({c['tipo']}" + ("" if c["canal"] == "telegram" else f", {c['canal']}") + ("" if c["pronto"] else ((", sem token" if c["canal"] != "whatsapp" else ", sem parear") if not values.get(c["segredos"]["token"] or "", "") and not (c.get("whatsapp") or {}).get("telefone") else ", sem grupo")) + ")" for c in catalogo))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="caminho do jarvis.yaml")
    ap.add_argument("--role", default="desktop", help="papéis separados por vírgula: desktop,core,knowledge")
    ap.add_argument("--home", required=True, help="$JARVIS_HOME")
    ap.add_argument("--kernel", required=True, help="pasta kernel/ do repositório")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-fewer-roles", action="store_true", help="permite renderizar menos papéis do que os instalados (o bootstrap usa ao adiar o knowledge ou quando a pessoa passa --role)")
    args = ap.parse_args()

    roles = [r.strip() for r in args.role.split(",") if r.strip()]
    bad = [r for r in roles if r not in ROLES]
    if bad or not roles:
        print(f"[render] papéis inválidos: {', '.join(bad) or '(vazio)'}", file=sys.stderr)
        return 2

    def for_roles(item: dict) -> bool:
        return any(r in item.get("roles", list(ROLES)) for r in roles)

    tenant_path = Path(args.tenant).expanduser().resolve()
    tenant_dir = tenant_path.parent
    kernel = Path(args.kernel).expanduser().resolve()
    home = Path(args.home).expanduser()
    user_home = Path.home()
    w = Writer(args.dry_run)
    versions = parse_env(kernel / "versions.env")

    data = yaml.safe_load(tenant_path.read_text(encoding="utf-8")) or {}
    schema = json.loads((kernel / "schema" / "jarvis.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        for err in errors:
            where = "/".join(str(p) for p in err.absolute_path) or "(raiz)"
            print(f"[render] jarvis.yaml inválido em {where}: {err.message}", file=sys.stderr)
        return 2
    tenant = data["tenant"]
    role_label = ",".join(roles)
    # Trava: numa instalação existente, o render com MENOS papéis do que os instalados apaga do secrets.env os
    # segredos dos papéis que ficaram de fora (e reescreve o profile.env). Só o bootstrap pode reduzir papéis,
    # de propósito (--allow-fewer-roles); um render à mão com --role parcial para e avisa.
    instalados = [x for x in parse_env(home / "profile.env").get("JARVIS_ROLE", "").split(",") if x]
    faltam = [x for x in instalados if x not in roles]
    if faltam and not args.allow_fewer_roles and not args.dry_run:
        print(f"[render] esta instalação tem os papéis {','.join(instalados)}; renderizar só {role_label} apagaria os "
              f"segredos de {','.join(faltam)}. Use ./bootstrap.sh --apply (lê os papéis instalados), passe todos os papéis, "
              f"ou --allow-fewer-roles se for mesmo para reduzir.", file=sys.stderr)
        return 2
    log(f"tenant={tenant} papéis={role_label} home={home}" + (" (dry-run)" if args.dry_run else ""))

    # ---- segredos: manifesto do kernel (por papel) + declarados no tenant + referenciados como ${X}
    manifest = yaml.safe_load((kernel / "secrets" / "manifest.yaml").read_text(encoding="utf-8"))
    entries: list[dict] = [dict(s) for s in manifest.get("secrets", []) if for_roles(s)]
    for s in data.get("segredos", []) or []:
        if for_roles(s):
            entries.append(dict(s))
    # assistentes do Telegram: cada um pede o token do próprio bot e, conforme o tipo, quem fala com ele.
    # Entram no catálogo de segredos com título em português; PRIMEIROS PASSOS mostra o que falta.
    assistentes = list(data.get("assistentes") or [])
    # os criados pelo JARVIS Web ficam em $JARVIS_HOME/assistentes.json (o jarvis.yaml pode ser o do kernel ou de
    # um pacote: não se edita). Skills padrão por tipo; nome repetido: o do JARVIS Web vale.
    try:
        loja = json.loads((home / "assistentes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loja = []
    kernel_skills = sorted(d.name for d in (kernel / "skills").iterdir() if (d / "SKILL.md").is_file())
    skills_padrao = {"mim": kernel_skills, "equipe": [s for s in ("knowledge", "knowledge-write") if s in kernel_skills], "cliente": [], "rotinas": []}
    for a in (loja if isinstance(loja, list) else []):
        if not isinstance(a, dict) or not a.get("nome"):
            continue
        if a.get("removido"):
            # apagado pela interface: o pacote ainda o declara, mas ele não existe mais aqui
            assistentes = [x for x in assistentes if x.get("nome") != a["nome"]]
            continue
        if a.get("tipo") not in skills_padrao:
            continue
        assistentes = [x for x in assistentes if x.get("nome") != a["nome"]]
        assistentes.append({"nome": a["nome"], "tipo": a["tipo"], "descricao": a.get("descricao", ""),
                            "canal": a.get("canal") if a.get("canal") in CANAIS else "telegram",
                            **({"whatsapp_modo": a["whatsapp_modo"]} if a.get("whatsapp_modo") in ("bot", "self-chat") else {}),
                            "skills": a.get("skills") if a.get("skills") is not None else skills_padrao[a["tipo"]],
                            **({"ajustes": a["ajustes"]} if a.get("ajustes") else {}),
                            **({"nivel": a["nivel"]} if a.get("nivel") in ("rapido", "equilibrado", "capaz") else {}),
                            **({"provedor": a["provedor"]} if a.get("provedor") in PROVEDORES_PAGINA else {}),
                            **({"do_pacote": True} if a.get("do_pacote") else {})})
    for a in assistentes:
        if canal_de(a) not in CANAIS:
            continue  # o render_assistentes reclama com a mensagem certa
        c, ch = CANAIS[canal_de(a)], chaves_assistente(a)
        rotulo = a.get("descricao") or a["nome"]
        if ch["token"]:
            entries.append({"name": ch["token"], "title": f"Token do bot do assistente {a['nome']}" + ("" if canal_de(a) == "telegram" else f" ({c['nome']})"),
                            "description": f"Token do bot deste assistente no {c['nome']} ({rotulo[:80]}). Um bot, um assistente.",
                            "roles": ["core"], "required": False, "obtain": c["obter_token"], "assistente": a["nome"]})
        if a["tipo"] in ("mim", "equipe"):
            entries.append({"name": ch["pessoas"], "title": f"Quem fala com o assistente {a['nome']}",
                            "description": "Identificações das pessoas (números, separados por vírgula)." + (" Você entra sozinho; vazio = só você." if canal_de(a) == "telegram" else " Vazio = ninguém ainda."),
                            "roles": ["core"], "required": False, "obtain": "a página ASSISTENTES reconhece quem manda 'oi' ao bot", "assistente": a["nome"]})
        if a["tipo"] in ("equipe", "cliente"):
            entries.append({"name": ch["grupos"], "title": f"{c['grupos_rotulo']} do assistente {a['nome']}",
                            "description": c["grupos_desc"] + (" Sem isso, um assistente para cliente não responde a ninguém." if a["tipo"] == "cliente" else ""),
                            "roles": ["core"], "required": False, "obtain": c["obter_grupos"], "assistente": a["nome"]})
    known = {s["name"] for s in entries}
    referenced = set(VAR_RE.findall(yaml.safe_dump(data, allow_unicode=True)))
    for name in sorted(referenced - known):
        entries.append({
            "name": name,
            "description": "referenciada no jarvis.yaml como ${...}; declare em `segredos:` para documentar",
            "required": False,
            "implicit": True,  # não declarada: para o MCP que a usa, vazia = MCP não funciona
        })
    secrets_path = home / "secrets.env"
    current = parse_env(secrets_path)
    values = {s["name"]: (current.get(s["name"]) or os.environ.get(s["name"], "")) for s in entries}
    generated: list[str] = []
    for s in entries:
        if s.get("generate") and not values[s["name"]]:
            values[s["name"]] = pysecrets.token_urlsafe(24)
            generated.append(s["name"])
    implicit_names = {s["name"] for s in entries if s.get("implicit")}
    missing = [s["name"] for s in entries if s.get("required") and not values[s["name"]]]
    empty = [s["name"] for s in entries if not values[s["name"]]]

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# secrets.env gerado pelo whitebeard-os. Modo 600. Nunca commitar, nunca copiar entre máquinas.",
        f"# tenant={tenant} papéis={role_label} gerado_em={stamp}",
        "# Preencha os valores vazios e rode o bootstrap de novo para aplicar.",
    ]
    for s in entries:
        name = s["name"]
        lines.append("")
        if s.get("description"):
            lines.append(f"# {s['description']}")
        if s.get("obtain"):
            lines.append(f"# como obter: {s['obtain']}")
        lines.append(f"# obrigatório: {'sim' if s.get('required') else 'não'}")
        lines.append(f"{name}={sq(values[name]) if values[name] else ''}")
    w.write(secrets_path, "\n".join(lines) + "\n", mode=0o600)

    # ---- cópia da declaração
    w.write(home / "jarvis.yaml", tenant_path.read_text(encoding="utf-8"))
    w.write(home / "tenant.path", str(tenant_path) + "\n")

    # ---- profile.sh
    # second brain: pasta do tenant em $HOME (padrão whitebeard-secondbrain). O bootstrap clona vault.repo
    # quando houver um e der acesso; senão cria um vault local com a estrutura do tenant.
    vault = data.get("vault") or {}
    vault_path = str(user_home / (vault.get("path") or "whitebeard-secondbrain"))
    # pasta de dados do tenant: `dados` (relativa a $HOME) ou $JARVIS_HOME/data; o JARVIS Web e as
    # skills leem $JARVIS_DATA, nunca um nome fixo
    data_dir = str(user_home / data["dados"]) if data.get("dados") else str(home / "data")
    if not args.dry_run:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
    profile = [
        f"# gerado pelo whitebeard-os para o tenant {tenant}; não editar (re-rode o bootstrap)",
        f"export JARVIS_HOME={sq(str(home))}",
        f"export JARVIS_TENANT={sq(tenant)}",
        f"export JARVIS_ROLE={sq(role_label)}",
        'export JARVIS_DATA="${JARVIS_DATA:-' + data_dir + '}"',
    ]
    if vault_path:
        profile.append('export VAULT_PATH="${VAULT_PATH:-' + vault_path + '}"')
    profile += [
        'export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"',
        'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac',
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"',
        'if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi',
    ]
    w.write(home / "profile.sh", "\n".join(profile) + "\n")
    # mesma informação em KEY=VALUE, sem shell: EnvironmentFile do systemd e LaunchAgent do macOS
    env_lines = [
        f"JARVIS_HOME={home}",
        f"JARVIS_TENANT={tenant}",
        f"JARVIS_ROLE={role_label}",
        f"JARVIS_DATA={data_dir}",
        f"HERMES_HOME={user_home / '.hermes'}",
    ]
    if vault_path:
        env_lines.append(f"VAULT_PATH={vault_path}")
    # /usr/sbin e /sbin: no macOS o ping mora em /sbin, e o serviço herda só este PATH
    env_lines.append(f"PATH={user_home / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    w.write(home / "profile.env", "\n".join(env_lines) + "\n")

    # ---- servidores MCP do Claude Code (papel desktop; segredos só por ${VAR} em runtime)
    tenant_mcp = [m for m in data.get("mcp", []) or [] if for_roles(m)]
    # cérebro local: o GBrain do papel knowledge entra como MCP `knowledge` no Claude Code e no Hermes, com o
    # token que o bootstrap gera ao instalar o knowledge (${VAR} literal: resolvido em runtime de secrets.env
    # no Claude Code e do .env no Hermes). O tenant pode declarar um `knowledge` próprio, que então prevalece.
    if "knowledge" in roles and values.get("GBRAIN_MCP_TOKEN") and not any(m["name"] == "knowledge" for m in tenant_mcp):
        tenant_mcp.append({"name": "knowledge", "transport": "http", "url": "http://127.0.0.1:3131/mcp",
                           "headers": {"Authorization": "Bearer ${GBRAIN_MCP_TOKEN}"}})
    servers = tenant_mcp if "desktop" in roles else []
    # o que o kernel registrou numa execução anterior e saiu do tenant é removido (nunca o que a pessoa
    # adicionou à mão: só nomes que estiveram nesta lista); vale também quando a lista ficou vazia
    # claude-mcp.names = só o que o KERNEL registrou (o script o escreve ao registrar; um MCP que já era da
    # pessoa e foi mantido nunca entra nele). O render não o reescreve: senão, na execução seguinte, os MCPs
    # da pessoa pareceriam do kernel e seriam substituídos.
    names_file = home / "render" / "claude-mcp.names"
    previous = names_file.read_text(encoding="utf-8").split() if names_file.is_file() else []
    stale = [n for n in previous if n not in {m["name"] for m in servers}]
    if servers or stale:
        kept = [n for n in previous if n not in stale]
        mcp = [
            "#!/bin/sh",
            f"# gerado pelo whitebeard-os a partir de {tenant_path}; idempotente",
            "set -eu",
            "set -a",
            f". {sq(str(secrets_path))}",
            "set +a",
            f"names={sq(str(names_file))}",
            # começa pela lista do que o kernel já registrou e continua no tenant; cada registro novo acrescenta
            "printf '%s' " + sq("".join(n + "\n" for n in kept)) + " >\"$names\"",
            "",
        ]
        for n in stale:
            mcp.append(f"claude mcp remove --scope user {sq(n)} >/dev/null 2>&1 && printf '[whitebeard-os] mcp %s removido (saiu do tenant)\\n' {sq(n)} || true")
        for m in servers:
            name = m["name"]
            # segredo vazio em env/headers/url: o MCP ficaria registrado quebrado (valores em branco) e,
            # pior, substituiria um registro que a pessoa já tinha funcionando; fica de fora, com aviso
            # ${VAR} não declarada em `segredos:` e vazia: o MCP não funcionaria (host, PAT, token de acesso).
            # Uma declarada como opcional (required: false) pode ficar vazia: o tenant disse que funciona sem.
            placeholders = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", json.dumps(m))))
            blanks = [n for n in placeholders if not values.get(n) and n in implicit_names]
            if blanks:
                mcp.append(f"printf '[whitebeard-os] aviso: mcp %s não registrado (segredo vazio: %s); preencha em secrets.env e rode de novo\\n' {sq(name)} {sq(', '.join(blanks))}")
                continue
            # um MCP com este nome que o kernel não registrou é da pessoa: fica como está (só os nomes em
            # claude-mcp.names, de execuções anteriores, são re-registrados)
            if name not in previous:
                mcp.append(f"if claude mcp get {sq(name)} >/dev/null 2>&1; then printf '[whitebeard-os] mcp %s já existia no Claude Code: mantido como está\\n' {sq(name)}; else")
            else:
                mcp.append(f"claude mcp remove --scope user {sq(name)} >/dev/null 2>&1 || true; if true; then")
            # Ordem importa: --env e --header são variádicos (engolem o que vier depois até a próxima
            # opção ou "--"), então vêm depois dos posicionais nome/url e antes de "--".
            cmd = ["claude", "mcp", "add", "--scope", "user", "--transport", m["transport"], sq(name)]
            if m["transport"] != "stdio":
                cmd.append(sq(m["url"]))
            for k, v in (m.get("headers") or {}).items():
                cmd += ["--header", dq(f"{k}: {v}")]
            for k, v in (m.get("env") or {}).items():
                cmd += ["--env", dq(f"{k}={v}")]
            if m["transport"] == "stdio":
                cmd += ["--", sq(m["command"])] + [sq(a) for a in m.get("args", []) or []]
            mcp.append("  " + " ".join(cmd) + f" >/dev/null 2>&1 && {{ printf '%s\\n' {sq(name)} >>\"$names\"; printf '[whitebeard-os] mcp %s registrado\\n' {sq(name)}; }}"
                       f" || printf '[whitebeard-os] aviso: não consegui registrar o mcp %s no Claude Code\\n' {sq(name)}")
            mcp.append("fi")
        w.write(home / "render" / "claude-mcp.sh", "\n".join(mcp) + "\n", mode=0o700)

    # ---- plugins do Claude Code (papel desktop): marketplaces e plugins que o tenant declara em `claude:`.
    # Script idempotente: só adiciona o que falta. Os nomes aplicados ficam em claude-plugins.names (o
    # uninstall desinstala só esses).
    claude_cfg = data.get("claude") or {}
    marketplaces = list(claude_cfg.get("marketplaces") or []) if "desktop" in roles else []
    plugins = list(claude_cfg.get("plugins") or []) if "desktop" in roles else []
    if marketplaces or plugins:
        lines = [
            "#!/bin/sh",
            f"# gerado pelo whitebeard-os a partir de {tenant_path}; idempotente",
            "set -u",
            "has_mp() { claude plugin marketplace list --json 2>/dev/null | python3 -c '",
            "import json, sys",
            "w = sys.argv[1].strip().lower().removesuffix(\".git\")",
            "for m in json.load(sys.stdin) or []:",
            "    vals = {str(m.get(k, \"\")).lower().removesuffix(\".git\") for k in (\"name\", \"repo\", \"url\", \"path\", \"installLocation\")}",
            "    if w in vals or any(v.endswith(\"/\" + w) or v.endswith(\":\" + w) for v in vals if v):",
            "        sys.exit(0)",
            "sys.exit(1)' \"$1\"; }",
            "has_plugin() { claude plugin list --json 2>/dev/null | python3 -c '",
            "import json, sys",
            "w = sys.argv[1].strip().lower(); name = w.split(\"@\")[0]",
            "for p in json.load(sys.stdin) or []:",
            "    vals = {str(v).lower() for v in p.values() if isinstance(v, str)}",
            "    if w in vals or name in vals or any(v.startswith(name + \"@\") for v in vals):",
            "        sys.exit(0)",
            "sys.exit(1)' \"$1\"; }",
            "yes_flag=''; claude plugin install --help 2>/dev/null | grep -q -- '-y, --yes' && yes_flag='-y'",
        ]
        for m in marketplaces:
            lines += [
                f"if has_mp {sq(m)}; then printf '[whitebeard-os] marketplace %s já registrado\\n' {sq(m)};",
                f"elif claude plugin marketplace add {sq(m)} </dev/null >/dev/null 2>&1; then printf '[whitebeard-os] marketplace %s registrado\\n' {sq(m)};",
                f"else printf '[whitebeard-os] aviso: não consegui registrar o marketplace %s\\n' {sq(m)}; fi",
            ]
        for pl in plugins:
            lines += [
                f"if has_plugin {sq(pl)}; then printf '[whitebeard-os] plugin %s já instalado\\n' {sq(pl)};",
                f"elif claude plugin install $yes_flag {sq(pl)} </dev/null >/dev/null 2>&1; then printf '[whitebeard-os] plugin %s instalado\\n' {sq(pl)};",
                f"else printf '[whitebeard-os] aviso: não consegui instalar o plugin %s (rode: claude plugin install %s)\\n' {sq(pl)} {sq(pl)}; fi",
            ]
        w.write(home / "render" / "claude-plugins.sh", "\n".join(lines) + "\n", mode=0o700)
        w.write(home / "render" / "claude-plugins.names", "\n".join(plugins) + "\n")

    # ---- papel core: Hermes (config inicial e variáveis), aplicados pelo bootstrap sem sobrescrever
    hermes_cfg = data.get("hermes") or {}
    hermes_mcp: dict[str, dict] = {}
    if "core" in roles or hermes_cfg:
        if hermes_cfg:
            model = {k: hermes_cfg[k] for k in ("provider", "model") if k in hermes_cfg}
            cfg = {"model": model}
            if hermes_cfg.get("fallback"):
                # "provedor/modelo" (divide no primeiro "/": openrouter/anthropic/x → provider openrouter);
                # sem "/", o provedor principal. O Hermes exige {provider, model}, não string.
                prov, sep, fb_model = hermes_cfg["fallback"].partition("/")
                cfg["fallback_model"] = ({"provider": prov, "model": fb_model} if sep
                                         else {"provider": model.get("provider", ""), "model": prov})
            w.write(home / "render" / "hermes" / "config.yaml",
                    "# gerado pelo whitebeard-os (modelo do tenant); o Hermes completa o resto\n"
                    + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        # Quem pensa: com escolha (página ou tenant), o modelo do JARVIS do dono também segue; o config.yaml acima
        # só vale na primeira instalação, então o bootstrap aplica estas duas chaves por cima a cada vez.
        qp = quem_pensa(home, kernel, data)
        if qp is not None and "core" in roles:
            m_dono, fb_dono = modelo_resolvido(qp, hermes_cfg)
            w.write(home / "render" / "hermes" / "modelo.yaml",
                    "# gerado pelo whitebeard-os: quem pensa (provedor e nível); aplicado por cima do config do Hermes\n"
                    + yaml.safe_dump({"model": m_dono, "fallback_model": fb_dono}, allow_unicode=True, sort_keys=False))
            log(f"quem pensa: {qp['tabela'][qp['provedor']]['nome']} · {qp['nivel']}" + (f" (reserva: {qp['tabela'][qp['reserva']]['nome']})" if qp['reserva'] else ""))
        elif not args.dry_run and (home / "render" / "hermes" / "modelo.yaml").exists():
            (home / "render" / "hermes" / "modelo.yaml").unlink()
        # MCPs do tenant para o Hermes deste host (o agente e o painel de MCPs do JARVIS Web leem dele).
        # ${VAR} fica literal: o Hermes resolve do próprio .env em runtime, e o segredo não vai para o config.
        for m in tenant_mcp:
            if m["transport"] == "stdio":
                entry = {"command": m["command"], "args": list(m.get("args", []) or [])}
            else:
                entry = {"url": m["url"]}
            for k in ("headers", "env"):
                if m.get(k):
                    entry[k] = dict(m[k])
            hermes_mcp[m["name"]] = entry
        # o Escritório ligado: o MCP `escritorio` (criar tarefa, aprovar, recusar) entra no Hermes do dono e nos
        # assistentes da equipe, como qualquer MCP do tenant; o Paperclip é local e sem chave no modo local
        if escritorio_ligado(home, data) and "core" in roles:
            hermes_mcp["escritorio"] = {"command": "python3", "args": [str(kernel / "bin" / "jarvis_escritorio_mcp.py")],
                                        "env": {"PAPERCLIP_API_URL": "http://127.0.0.1:3100/api", "ESCRITORIO_ORIGEM": "telegram", "JARVIS_HOME": str(home)}}
        w.write(home / "render" / "hermes" / "mcp.json",
                json.dumps(hermes_mcp, ensure_ascii=False, indent=2) + "\n")
        mcp_refs = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", json.dumps(hermes_mcp))))
        env_keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
        env_keys += [k for k in mcp_refs if k not in env_keys]
        env_lines = [f"{k}={values[k]}" for k in env_keys if values.get(k)]
        # as skills de documentação falam em $VAULT_PATH e $JARVIS_TENANT: o gateway não herda o shell da
        # pessoa, então o agente do Hermes recebe os dois pelo .env (não são segredos)
        env_lines += [f"VAULT_PATH={vault_path}", f"JARVIS_TENANT={tenant}"]
        # chat privado (id positivo) é o próprio dono: autorizado no gateway sem pareamento manual
        home_chat = str(values.get("TELEGRAM_HOME_CHANNEL", "")).strip()
        if home_chat.isdigit():
            env_lines.append(f"TELEGRAM_ALLOWED_USERS={home_chat}")
        w.write(home / "render" / "hermes" / "env",
                "# gerado pelo whitebeard-os; só chaves preenchidas em secrets.env\n" + "\n".join(env_lines) + "\n",
                mode=0o600)

    # ---- assistentes do Telegram (papel core): um perfil isolado do Hermes por assistente. O render escreve
    # o que o perfil deve ter (config, variáveis, persona, skills); o bootstrap cria o perfil e aplica.
    if "core" in roles and assistentes:
        rc = render_assistentes(w, home, data, assistentes, values, tenant, vault_path, hermes_mcp if "core" in roles or hermes_cfg else {}, installed_skills_hint(kernel, tenant_dir, data))
        if rc:
            return rc
    elif not args.dry_run and (home / "render" / "hermes" / "assistentes.json").exists():
        # saíram todos do tenant: a lista vazia faz o bootstrap estacionar os perfis que ele criou
        w.write(home / "render" / "hermes" / "assistentes.json", "[]\n")
        limpar_assistentes_antigos(w, home, [])

    # ---- Escritório (papel core): o cargo "Assistente de trabalho" é um perfil do Hermes executado pelo
    # Paperclip (adapter hermes_local), com as ferramentas de um assistente de equipe e o MCP do Escritório
    # para entregar e pedir aprovação. O bootstrap aplica como os assistentes e cria o cargo no Paperclip.
    if "core" in roles and escritorio_ligado(home, data):
        cargos, rotinas = catalogo_escritorio(home, data)
        tools = sorted(FERRAMENTAS_TIPO["equipe"])
        skills_cargo = "\n".join(s_ for s_ in ("knowledge", "knowledge-write", "project-documentation") if s_ in installed_skills_hint(kernel, tenant_dir, data)) + "\n"
        for c in cargos:
            base = home / "render" / "hermes" / "cargos" / c["nome"]
            m_cargo, fb_cargo = modelo_resolvido(quem_pensa(home, kernel, data), hermes_cfg, c.get("nivel"))
            cfg_cargo: dict = {"model": m_cargo, "platform_toolsets": {"cli": tools}}
            if fb_cargo:
                cfg_cargo["fallback_model"] = fb_cargo
            mcp_cargo = dict(hermes_mcp)
            # o servidor MCP não herda o ambiente do heartbeat: as credenciais da execução (chave do agente, id da
            # execução e da tarefa) entram por ${VAR}, que o Hermes expande ao carregar a config dentro do heartbeat.
            # Com elas, entregar/pedir_aprovacao são do próprio agente e não acordam o cargo de novo.
            mcp_cargo["escritorio"] = {"command": "python3", "args": [str(kernel / "bin" / "jarvis_escritorio_mcp.py")],
                                       "env": {"PAPERCLIP_API_URL": "http://127.0.0.1:3100/api", "PAPERCLIP_API_KEY": "${PAPERCLIP_API_KEY}",
                                               "PAPERCLIP_RUN_ID": "${PAPERCLIP_RUN_ID}", "PAPERCLIP_TASK_ID": "${PAPERCLIP_TASK_ID}", "JARVIS_HOME": str(home)}}
            cfg_cargo["mcp_servers"] = mcp_cargo
            env_cargo = [f"VAULT_PATH={vault_path}", f"JARVIS_TENANT={tenant}", f"WHITEBEARD_CARGO={c['nome']}"]
            for k in ("GBRAIN_MCP_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if values.get(k):
                    env_cargo.append(f"{k}={values[k]}")
            soul = "\n".join([
                f"Você é o cargo {c['titulo']} do Escritório do Whitebeard OS, tenant {tenant}.",
                c.get("faz") or "Você executa tarefas delegadas pelo JARVIS e pelos assistentes: pesquisas, documentos, propostas, resumos.",
                "",
                "Regras que não mudam:",
                "- A entrega é sempre um arquivo Markdown em `entregas/` na pasta de trabalho; depois, a ferramenta `entregar`.",
                "- Ação fora deste computador (enviar, pagar, publicar, comprar) nunca é feita por você: `pedir_aprovacao` e pare.",
                "- Você não roda comandos neste computador e não lê o que está fora da pasta de trabalho e do second brain.",
                "- Nunca invente dados; diga o que faltou.",
                "- Responda em português do Brasil, curto e direto.",
                ""])
            w.write(base / "config.yaml", f"# gerado pelo whitebeard-os para o cargo {c['nome']} (Escritório)\n" + yaml.safe_dump(cfg_cargo, allow_unicode=True, sort_keys=False))
            w.write(base / "env", "# gerado pelo whitebeard-os; variáveis do perfil do Hermes do cargo\n" + "\n".join(env_cargo) + "\n", mode=0o600)
            w.write(base / "SOUL.md", soul)
            w.write(base / "skills.txt", skills_cargo)
        # cargo que saiu do catálogo: o render dele some (o bootstrap só aplica o que está em render/hermes/cargos/)
        if not args.dry_run:
            for d in (home / "render" / "hermes" / "cargos").glob("*/"):
                if d.is_dir() and d.name not in {c["nome"] for c in cargos}:
                    shutil.rmtree(d, ignore_errors=True)
        w.write(home / "render" / "escritorio.json", json.dumps({"ligado": True, "cargos": cargos, "rotinas": rotinas}, ensure_ascii=False, indent=2) + "\n")
        log(f"escritório: ligado ({len(cargos)} cargo(s) executado(s) pelo Hermes: {', '.join(c['nome'] for c in cargos)}; {len(rotinas)} rotina(s) do tenant; MCP escritorio no JARVIS e na equipe)")
    elif not args.dry_run and (home / "render" / "escritorio.json").exists():
        w.write(home / "render" / "escritorio.json", json.dumps({"ligado": False, "cargos": [], "rotinas": []}) + "\n")

    # ---- papel desktop: board key do Paperclip para o JARVIS Web. O serviço não recebe segredos pelo
    # ambiente (o plist do LaunchAgent é legível); o app lê este arquivo da pasta de dados.
    # Só cria: um arquivo existente (feito à mão, com outra chave) não é trocado em silêncio.
    if "desktop" in roles and values.get("PAPERCLIP_API_KEY"):
        key_file = Path(data_dir) / "paperclip-board-key"
        if not key_file.exists():
            w.write(key_file, values["PAPERCLIP_API_KEY"] + "\n", mode=0o600)
        elif key_file.read_text(encoding="utf-8").strip() != values["PAPERCLIP_API_KEY"]:
            log(f"aviso: {key_file} tem outra chave que a PAPERCLIP_API_KEY de secrets.env; mantido como está")

    # ---- papel knowledge: .env do compose (imagens do lockfile + segredos gerados)
    if "knowledge" in roles:
        if vault_path and Path(vault_path).is_dir():
            vault_index = Path(vault_path)
        else:
            vault_index = home / "vault-index"
            if not args.dry_run:
                vault_index.mkdir(parents=True, exist_ok=True)
        kenv = {
            "PGVECTOR_IMAGE": versions.get("PGVECTOR_IMAGE", "pgvector/pgvector:pg17"),
            "OLLAMA_IMAGE": versions.get("OLLAMA_IMAGE", "ollama/ollama:latest"),
            "BUN_IMAGE": versions.get("BUN_IMAGE", "oven/bun:1.4"),
            "GBRAIN_REF": versions.get("GBRAIN_REF", "master"),
            "EMBED_MODEL": versions.get("EMBED_MODEL", "bge-m3"),
            "POSTGRES_PASSWORD": values.get("POSTGRES_PASSWORD", ""),
            "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": values.get("GBRAIN_ADMIN_BOOTSTRAP_TOKEN", ""),
            "ANTHROPIC_API_KEY": values.get("ANTHROPIC_API_KEY", ""),
            "GBRAIN_PUBLIC_URL": "http://localhost:3131",
            "GBRAIN_PORT": "3131",
            "OLLAMA_PORT": "11434",
            "VAULT_INDEX_DIR": str(vault_index),
            "ACELERACAO": str(data.get("aceleracao", "auto")),
        }
        w.write(home / "render" / "knowledge.env",
                "# gerado pelo whitebeard-os para docker compose; modo 600\n"
                + "\n".join(f"{k}={v}" for k, v in kenv.items()) + "\n", mode=0o600)

    # ---- skills: kernel + packs do tenant → $JARVIS_HOME/skills, linkadas em ~/.claude/skills
    packs = [kernel / "skills"] + [
        (tenant_dir / p).resolve() for p in (data.get("skills") or {}).get("packs", []) or []
    ]
    installed: list[str] = []
    hermes_existing = None  # mapa nome→pasta das skills já instaladas no Hermes (feito uma vez)
    not_linked: list[str] = []
    claude_skills = user_home / ".claude" / "skills"
    for pack in packs:
        if not pack.is_dir():
            log(f"pack de skills ausente: {pack}")
            continue
        for d in sorted(pack.iterdir()):
            if d.name.startswith(".") or not (d / "SKILL.md").is_file():
                continue
            dest = home / "skills" / d.name
            link = claude_skills / d.name
            if args.dry_run:
                log(f"instalaria skill {d.name} <- {d}")
                installed.append(d.name)
                continue
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            elif dest.is_dir():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(d, dest)
            claude_skills.mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                link.unlink()
            if link.exists():
                not_linked.append(str(link))
            else:
                link.symlink_to(dest)
            # o Hermes lê as próprias skills em ~/.hermes/skills (mesmo formato SKILL.md): o agente do
            # Telegram e do chat documenta com as mesmas regras do Claude Code
            hermes_skills = user_home / ".hermes" / "skills"
            if hermes_skills.is_dir():
                hl = hermes_skills / d.name
                # o Hermes organiza skills em categorias (skills/<categoria>/.../<nome>); uma skill com o mesmo
                # nome já instalada em qualquer lugar é a da pessoa e vale: não se cria um duplicado
                if hermes_existing is None:
                    hermes_existing = {}
                    for q in hermes_skills.rglob("SKILL.md"):
                        sk = q.parent
                        if sk.parent == hermes_skills and sk.is_symlink():
                            continue  # links planos do próprio kernel
                        hermes_existing.setdefault(sk.name.lower(), sk)
                if hl.is_symlink():
                    hl.unlink()
                if d.name.lower() in hermes_existing:
                    where = hermes_existing[d.name.lower()].relative_to(hermes_skills).parent
                    log(f"skill {d.name}: o Hermes já a tem em {where}/; sem link duplicado")
                elif not hl.exists():
                    hl.symlink_to(dest)
            installed.append(d.name)

    # ---- resumo
    summary = [
        f"[whitebeard-os] tenant: {tenant} | papéis: {role_label} | fonte: {tenant_path}",
        f"[whitebeard-os] skills instaladas ({len(installed)}): {', '.join(installed) or '-'}",
        f"[whitebeard-os] mcp ({len(servers)}): {', '.join(m['name'] for m in servers) or '-'}",
        f"[whitebeard-os] plugins ({len(plugins)}): {', '.join(plugins) or '-'}",
        f"[whitebeard-os] assistentes ({len(assistentes)}): {', '.join(a['nome'] for a in assistentes) or 'só o do dono'}",
        f"[whitebeard-os] segredos: {len(entries)} esperados, {len(empty)} vazios"
        + (f", {len(generated)} gerados ({', '.join(generated)})" if generated else "")
        + (f"; OBRIGATÓRIOS faltando: {', '.join(missing)}" if missing else ""),
    ]
    if vault_path:
        summary.append(f"[whitebeard-os] vault: {vault_path}" + ("" if Path(vault_path).exists() else " (o bootstrap cria)"))
    summary.append(f"[whitebeard-os] dados: {data_dir}")
    if "knowledge" in roles:
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            summary.append(f"[whitebeard-os] knowledge: nativo (GBrain {versions.get('GBRAIN_REF', '?')} com PGLite, Ollama na GPU, embeddings {versions.get('EMBED_MODEL', '?')}); MCP knowledge em http://127.0.0.1:3131/mcp")
        else:
            summary.append(f"[whitebeard-os] knowledge: compose em {home / 'compose' / 'knowledge'}; GBrain {versions.get('GBRAIN_REF', '?')}, embeddings {versions.get('EMBED_MODEL', '?')}")
    if not_linked:
        summary.append(f"[whitebeard-os] aviso: não linkei (destino existe e não é link): {', '.join(not_linked)}")
    if missing:
        summary.append(f"[whitebeard-os] preencha {secrets_path} e rode o bootstrap de novo")
    # módulos do JARVIS Web: o tenant escolhe; sem `interface`, só os genéricos do produto
    modulos = ((data.get("interface") or {}).get("modulos")) or MODULOS_PADRAO
    w.write(home / "render" / "web.json", json.dumps({"tenant": tenant, "modulos": list(modulos)}, ensure_ascii=False, indent=2) + "\n")
    # catálogo dos segredos esperados (nomes e metadados, NUNCA valores): o jarvis_setup.py e a página
    # PRIMEIROS PASSOS do JARVIS Web mostram o que falta sem ler comentários do secrets.env
    w.write(home / "render" / "secrets.json", json.dumps([{
        "nome": s["name"],
        "titulo": s.get("title", ""),
        "descricao": s.get("description", ""),
        "obter": s.get("obtain", ""),
        "obrigatorio": bool(s.get("required")),
        "gerado": bool(s.get("generate")),
        "implicito": bool(s.get("implicit")),
        "assistente": s.get("assistente", ""),
    } for s in entries], ensure_ascii=False, indent=2) + "\n")
    # obrigatórios ainda vazios: o bootstrap pergunta no terminal e roda o render de novo
    w.write(home / "render" / "secrets-missing.txt", "\n".join(missing) + ("\n" if missing else ""))
    w.write(home / "render" / "summary.txt", "\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
