#!/usr/bin/env python3
"""Testes do conversor da 0.4.0 (kernel/bin/migrate_0_4.py): arquivos de estado, jarvis.yaml, addon.yaml e
preferências, numa pasta temporária. Só biblioteca padrão; a validação contra o esquema usa o `uv`, se houver.

Uso: python3 test/test_migrate_0_4.py   (ou python3 -m unittest test/test_migrate_0_4.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATE = REPO / "kernel" / "bin" / "migrate_0_4.py"
SCHEMA = REPO / "kernel" / "schema" / "jarvis.schema.json"

OLD_YAML = textwrap.dedent("""\
    # tenant de teste, no formato de antes da 0.4.0
    tenant: pessoal
    idioma: pt-BR
    aceleracao: auto
    dados: .teste # comentário fica
    interface:
      modulos:
        - sistemas
        - operacao # painéis
        - instagram
    mcp:
      - name: ferramenta
        transport: stdio
        command: npx
        env:
          nome: "${NOME_X}" # variável do tenant: não se traduz
        roles: [desktop, satelite]
    exportacao:
      grupos:
        dados: ["*.csv"]  # nome de grupo do tenant: não se traduz
    decisoes:
      provider: regras
      limiar_confianca: 0.9
      perfis_excluidos: [cliente-a]
    segredos:
      - name: X_TOKEN
        roles:
          - satelite
    hermes:
      provedor: chatgpt
      nivel: rapido
      provider: anthropic
      model: claude-sonnet-5-5
    perfis:
      - nome: cliente-a
        tipo: grupo-cliente
    crons:
      - nome: diario
        agenda: "0 9 * * *"
        perfil: cliente-a
        prompt: |
          nome: isto é texto do prompt, não uma chave
          tipo: idem
    servicos_monitorados: []
    assistentes:
      - nome: suporte
        tipo: equipe
        canal: discord
        descricao: "Ajuda a equipe"
        ajustes:
          computador: true
          anotar: false
        nivel: capaz
        modelo: claude-opus-5-5
      - nome: zap
        tipo: mim
        canal: whatsapp
        whatsapp_modo: self-chat
    escritorio:
      ligado: true
      cargos:
        - nome: trabalho
          titulo: Assistente
          faz: tudo
          nivel: equilibrado
      rotinas:
        - nome: resumo-da-semana
          titulo: Resumo
          faz: resume
          quando: "0 8 * * 1"
          onde: 'assistente:suporte'
          cargo: trabalho
          ligada: false
    nos:
      - {host: local, papeis: [desktop, core, satelite]}
      - host: outro
        papel: knowledge
    """)

NEW_YAML = textwrap.dedent("""\
    # tenant de teste, no formato de antes da 0.4.0
    tenant: personal
    language: pt-BR
    acceleration: auto
    data: .teste # comentário fica
    interface:
      modules:
        - systems
        - operations # painéis
        - instagram
    mcp:
      - name: ferramenta
        transport: stdio
        command: npx
        env:
          nome: "${NOME_X}" # variável do tenant: não se traduz
        roles: [desktop, satellite]
    export:
      groups:
        dados: ["*.csv"]  # nome de grupo do tenant: não se traduz
    decisions:
      provider: rules
      confidence_threshold: 0.9
      excluded_profiles: [cliente-a]
    secrets:
      - name: X_TOKEN
        roles:
          - satellite
    hermes:
      subscription: chatgpt
      level: fast
      provider: anthropic
      model: claude-sonnet-5-5
    profiles:
      - name: cliente-a
        type: client-group
    crons:
      - name: diario
        schedule: "0 9 * * *"
        profile: cliente-a
        prompt: |
          nome: isto é texto do prompt, não uma chave
          tipo: idem
    monitored_services: []
    assistants:
      - name: suporte
        type: team
        channel: discord
        description: "Ajuda a equipe"
        settings:
          computer: true
          write_notes: false
        level: capable
        model: claude-opus-5-5
      - name: zap
        type: owner
        channel: whatsapp
        whatsapp_mode: self-chat
    office:
      enabled: true
      positions:
        - name: work
          title: Assistente
          does: tudo
          level: balanced
      routines:
        - name: weekly-summary
          title: Resumo
          does: resume
          when: "0 8 * * 1"
          where: 'assistant:suporte'
          position: work
          enabled: false
    nodes:
      - {host: local, roles: [desktop, core, satellite]}
      - host: outro
        role: knowledge
    """)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MIGRATE), *args], capture_output=True, text=True, check=False)


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace") for p in sorted(root.rglob("*")) if p.is_file()}


def old_home(home: Path) -> None:
    """Uma instalação de antes da 0.4.0, com um arquivo de cada tipo."""
    write(home / "assistentes.json", [
        {"nome": "suporte", "tipo": "equipe", "canal": "telegram", "descricao": "d", "criado_em": "2026-11-01T10:00:00-0300",
         "ajustes": {"computador": True, "anotar": False}, "nivel": "rapido", "provedor": "chatgpt", "bot": "sup_bot",
         "pessoas_nomes": {"123": "Ana"}, "grupos_nomes": {"-100": "Grupo"}, "skills": ["knowledge"]},
        {"nome": "velho", "removido": True},
        {"nome": "zap", "tipo": "cliente", "canal": "whatsapp", "whatsapp_modo": "bot", "do_pacote": True},
    ])
    write(home / "escritorio.json", {"ligado": True, "cargos": [{"nome": "redator", "titulo": "Redator", "faz": "escreve", "nivel": "capaz"},
                                                                 {"nome": "x", "removido": True}]})
    write(home / "escritorio" / "cargos.json", {"trabalho": {"id": "a1", "titulo": "Assistente de trabalho", "empresa": "c1"},
                                                "redator": {"id": "a2", "titulo": "Redator", "pausado": True}})
    write(home / "escritorio" / "cargo.json", {"id": "a1", "nome": "Assistente de trabalho", "empresa": "c1", "pasta": "/v"})
    write(home / "escritorio" / "rotinas.json", [{"nome": "status", "titulo": "S", "faz": "f", "quando": "0 8 * * *",
                                                  "onde": "assistente:suporte", "cargo": "trabalho", "ligada": True},
                                                 {"nome": "resumo-da-semana", "removido": True}])
    write(home / "escritorio" / "carteiro.json", {"avisadas": {"i1": "done", "i2:p1": "pendencia"}, "inicio": 100,
                                                  "rotinas": {"resumo-da-semana": {"ultima": "2026-11-16T08:00", "tarefa": "PES-1", "em": 5}}})
    write(home / "escritorio" / "tokens.json", {"mes": {}})
    write(home / "pensa.json", {"provedor": "chatgpt", "nivel": "capaz", "logados": ["chatgpt"]})
    write(home / "pensa.cache.json", {"chatgpt": {"logado": True, "em": 1.0}})
    write(home / "pensa.login.json", {"provedor": "chatgpt", "estado": "conectado", "pid": 1, "inicio": 2, "endereco": "u", "codigo": "C"})
    write(home / "logs" / "pensa-login.log", "log\n")
    write(home / "telegram.decisao", "nao\n")
    write(home / "assistentes" / "zap.whatsapp.json", {"estado": "esperando", "telefone": "55", "atualizado_em": 3})
    write(home / "assistentes" / "zap" / "whatsapp" / "session" / "creds.json", {"me": {"id": "55:1"}})
    write(home / "update.run.json", {"inicio": 1, "log": "l", "de": {"web": "1", "kernel": "k", "ramo": "main"}, "unidade": "u"})
    write(home / "render" / "hermes" / "assistentes.json", [{"nome": "suporte", "tipo": "equipe", "segredos": {"token": "T", "pessoas": "P", "grupos": None},
                                                             "pronto": True}])
    write(home / "render" / "hermes" / "assistentes.applied", "suporte\n")
    write(home / "render" / "hermes" / "assistentes" / "suporte" / "env", "WHITEBEARD_ASSISTENTE=suporte\n")
    write(home / "render" / "hermes" / "cargos" / "trabalho" / "env", "WHITEBEARD_CARGO=trabalho\n")
    write(home / "render" / "hermes" / "modelo.yaml", "model: {}\n")
    write(home / "render" / "escritorio.json", {"ligado": True, "cargos": [{"nome": "trabalho", "titulo": "T", "faz": ""}], "rotinas": []})
    write(home / "render" / "secrets.json", [{"nome": "A", "titulo": "t", "descricao": "d", "obter": "o", "obrigatorio": False,
                                              "gerado": False, "implicito": False, "assistente": ""}])
    write(home / "render" / "web.json", {"tenant": "pessoal", "modulos": ["sistemas", "conhecimento", "camera"]})
    write(home / "secrets.env", "# x\nGITHUB_LEITURA_TOKEN='abc'\nOUTRO=1\n")


class MigrateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="migrate-test-"))
        self.home = self.tmp / ".jarvis"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ jarvis.yaml
    def test_tenant_yaml_rewritten_with_comments_and_backup(self) -> None:
        f = write(self.tmp / "jarvis.yaml", OLD_YAML)
        r = run("--tenant", str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(f.read_text(encoding="utf-8"), NEW_YAML)
        self.assertEqual((self.tmp / "jarvis.yaml.bak").read_text(encoding="utf-8"), OLD_YAML)
        self.assertIn("idioma: pt-BR → language: pt-BR", r.stdout)
        self.assertIn("mova a pasta dele no vault", r.stdout)

    def test_tenant_yaml_idempotent(self) -> None:
        f = write(self.tmp / "jarvis.yaml", OLD_YAML)
        run("--tenant", str(f))
        r = run("--tenant", str(f))
        self.assertEqual(r.returncode, 0)
        self.assertIn("nada a fazer", r.stdout)
        self.assertEqual(f.read_text(encoding="utf-8"), NEW_YAML)
        self.assertEqual((self.tmp / "jarvis.yaml.bak").read_text(encoding="utf-8"), OLD_YAML)

    def test_kernel_tenants_are_already_migrated(self) -> None:
        for t in ("example", "personal"):
            with self.subTest(tenant=t):
                f = shutil.copy(REPO / "tenants" / t / "jarvis.yaml", self.tmp / f"{t}.yaml")
                r = run("--tenant", str(f), "--addon", str(REPO / "tenants" / "example" / "addon-example" / "addon.yaml"), "--dry-run")
                self.assertIn("nada a fazer", r.stdout, r.stdout)

    @unittest.skipUnless(shutil.which("uv"), "sem uv: a validação contra o esquema precisa de PyYAML e jsonschema")
    def test_migrated_yaml_passes_the_schema(self) -> None:
        f = write(self.tmp / "jarvis.yaml", OLD_YAML)
        run("--tenant", str(f))
        code = ("import json, sys, yaml\nfrom jsonschema import Draft202012Validator\n"
                "s = json.load(open(sys.argv[1])); d = yaml.safe_load(open(sys.argv[2]))\n"
                "e = [x.message for x in Draft202012Validator(s).iter_errors(d)]\nprint(e); sys.exit(1 if e else 0)\n")
        env = dict(os.environ, UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"))
        r = subprocess.run(["uv", "run", "--quiet", "--with", "pyyaml>=6,<7", "--with", "jsonschema>=4,<5", "python3", "-c", code,
                            str(SCHEMA), str(f)], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_old_yaml_is_refused_by_the_schema_keys(self) -> None:
        """As chaves antigas não existem no esquema (o render recusa um jarvis.yaml antigo)."""
        props = json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]
        for old in ("idioma", "segredos", "assistentes", "escritorio", "nos", "dados", "perfis"):
            self.assertNotIn(old, props)

    # ------------------------------------------------------------ addon.yaml
    def test_addon_yaml(self) -> None:
        f = write(self.tmp / "addon.yaml", "addon: x # nome\ndescricao: um add-on\nsegredos:\n  - name: X\n    roles: [core, satelite]\n")
        r = run("--addon", str(f))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(f.read_text(encoding="utf-8"),
                         "addon: x # nome\ndescription: um add-on\nsecrets:\n  - name: X\n    roles: [core, satellite]\n")

    # ------------------------------------------------------------ $JARVIS_HOME
    def test_home_state_files(self) -> None:
        old_home(self.home)
        r = run("--home", str(self.home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        h = self.home
        for gone in ("assistentes.json", "escritorio.json", "escritorio", "pensa.json", "pensa.cache.json", "pensa.login.json",
                     "telegram.decisao", "assistentes", "logs/pensa-login.log", "render/hermes/assistentes.json",
                     "render/hermes/assistentes.applied", "render/hermes/assistentes", "render/hermes/cargos",
                     "render/hermes/modelo.yaml", "render/escritorio.json", "office/tokens.json"):
            self.assertFalse((h / gone).exists(), gone)
        a = read(h / "assistants.json")
        self.assertEqual(a[0], {"name": "suporte", "type": "team", "channel": "telegram", "description": "d",
                                "created_at": "2026-11-01T10:00:00-0300", "settings": {"computer": True, "write_notes": False},
                                "level": "fast", "provider": "chatgpt", "bot": "sup_bot", "people_names": {"123": "Ana"},
                                "group_names": {"-100": "Grupo"}, "skills": ["knowledge"]})
        self.assertEqual(a[1], {"name": "velho", "removed": True})
        self.assertEqual(a[2], {"name": "zap", "type": "client", "channel": "whatsapp", "whatsapp_mode": "bot", "from_pack": True})
        self.assertEqual(read(h / "office.json"), {"enabled": True, "positions": [
            {"name": "redator", "title": "Redator", "does": "escreve", "level": "capable"}, {"name": "x", "removed": True}]})
        self.assertEqual(read(h / "office" / "positions.json"), {"work": {"id": "a1", "title": "Assistente de trabalho", "company": "c1"},
                                                                "redator": {"id": "a2", "title": "Redator", "paused": True}})
        self.assertEqual(read(h / "office" / "position.json"), {"id": "a1", "name": "Assistente de trabalho", "company": "c1", "folder": "/v"})
        self.assertEqual(read(h / "office" / "routines.json"), [
            {"name": "status", "title": "S", "does": "f", "when": "0 8 * * *", "where": "assistant:suporte", "position": "work", "enabled": True},
            {"name": "weekly-summary", "removed": True}])
        self.assertEqual(read(h / "office" / "courier.json"), {"notified": {"i1": "done", "i2:p1": "pending"}, "started_at": 100,
                                                               "routines": {"weekly-summary": {"last": "2026-11-16T08:00", "task": "PES-1", "at": 5}}})
        self.assertEqual(read(h / "thinker.json"), {"provider": "chatgpt", "level": "capable", "logged_in": ["chatgpt"]})
        self.assertEqual(read(h / "thinker.cache.json"), {"chatgpt": {"logged_in": True, "at": 1.0}})
        self.assertEqual(read(h / "thinker.login.json"), {"provider": "chatgpt", "state": "connected", "pid": 1, "started_at": 2, "url": "u", "code": "C"})
        self.assertTrue((h / "logs" / "thinker-login.log").is_file())
        self.assertEqual((h / "telegram.decision").read_text(encoding="utf-8"), "no\n")
        self.assertEqual(read(h / "assistants" / "zap.whatsapp.json"), {"state": "waiting", "phone": "55", "updated_at": 3})
        self.assertTrue((h / "assistants" / "zap" / "whatsapp" / "session" / "creds.json").is_file())
        self.assertEqual(read(h / "update.run.json"), {"started_at": 1, "log": "l", "from": {"web": "1", "kernel": "k", "branch": "main"}, "unit": "u"})
        self.assertEqual(read(h / "render" / "hermes" / "assistants.json"),
                         [{"name": "suporte", "type": "team", "secrets": {"token": "T", "people": "P", "groups": None}, "ready": True}])
        self.assertTrue((h / "render" / "hermes" / "assistants.applied").is_file())
        self.assertTrue((h / "render" / "hermes" / "assistants" / "suporte" / "env").is_file())
        self.assertTrue((h / "render" / "hermes" / "positions" / "trabalho" / "env").is_file())
        self.assertTrue((h / "render" / "hermes" / "model.yaml").is_file())
        self.assertEqual(read(h / "render" / "office.json"), {"enabled": True, "positions": [{"name": "work", "title": "T", "does": ""}], "routines": []})
        self.assertEqual(read(h / "render" / "secrets.json"), [{"name": "A", "title": "t", "description": "d", "obtain": "o", "required": False,
                                                               "generated": False, "implicit": False, "assistant": ""}])
        self.assertEqual(read(h / "render" / "web.json"), {"tenant": "personal", "modules": ["systems", "knowledge", "camera"]})
        self.assertEqual((h / "secrets.env").read_text(encoding="utf-8"), "# x\nGITHUB_READ_TOKEN='abc'\nOUTRO=1\n")
        self.assertIn("hermes profile delete trabalho", r.stdout)

    def test_home_idempotent(self) -> None:
        old_home(self.home)
        run("--home", str(self.home))
        before = snapshot(self.home)
        r = run("--home", str(self.home))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("nada a fazer", r.stdout)
        self.assertEqual(before, snapshot(self.home))

    def test_dry_run_changes_nothing(self) -> None:
        old_home(self.home)
        f = write(self.tmp / "jarvis.yaml", OLD_YAML)
        before = snapshot(self.tmp)
        r = run("--home", str(self.home), "--tenant", str(f), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("(dry-run) renomeado", r.stdout)
        self.assertIn("(dry-run) chaves traduzidas", r.stdout)
        self.assertEqual(before, snapshot(self.tmp))

    def test_old_and_new_both_present_is_not_overwritten(self) -> None:
        write(self.home / "assistentes.json", [{"nome": "a"}])
        write(self.home / "assistants.json", [{"name": "b"}])
        r = run("--home", str(self.home))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("existem os dois", r.stdout)
        self.assertEqual(read(self.home / "assistentes.json"), [{"nome": "a"}])
        self.assertEqual(read(self.home / "assistants.json"), [{"name": "b"}])

    def test_secret_keeps_existing_new_value(self) -> None:
        write(self.home / "secrets.env", "GITHUB_LEITURA_TOKEN='abc'\nGITHUB_READ_TOKEN=\n")
        run("--home", str(self.home))
        self.assertEqual((self.home / "secrets.env").read_text(encoding="utf-8"), "GITHUB_READ_TOKEN='abc'\n")

    def test_tenant_path_and_addons_point_to_new_folders(self) -> None:
        self.home.mkdir()
        write(self.home / "tenant.path", str(REPO / "tenants" / "pessoal" / "jarvis.yaml") + "\n")
        write(self.home / "addons.json", [{"addon": "exemplo-addon", "path": str(REPO / "tenants" / "exemplo" / "addon-exemplo" / "addon.yaml"),
                                           "skills": ["exemplo-addon-ola"]}])
        run("--home", str(self.home))
        self.assertEqual((self.home / "tenant.path").read_text(encoding="utf-8").strip(), str(REPO / "tenants" / "personal" / "jarvis.yaml"))
        self.assertEqual(read(self.home / "addons.json"), [{"addon": "example-addon", "skills": ["example-addon-hello"],
                                                           "path": str(REPO / "tenants" / "example" / "addon-example" / "addon.yaml")}])

    # ------------------------------------------------------------ $JARVIS_DATA
    def test_data_preferences(self) -> None:
        data = self.tmp / ".whitebeard"
        write(data / "preferencias.json", {"idioma": "en", "tema": "atlas", "escalaTexto": 1.15})
        r = run("--data", str(data))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((data / "preferencias.json").exists())
        self.assertEqual(read(data / "preferences.json"), {"language": "en", "theme": "atlas", "text_scale": 1.15})

    def test_needs_something_to_do(self) -> None:
        r = run()
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
