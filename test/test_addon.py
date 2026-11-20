#!/usr/bin/env python3
"""Testes de aceite do mecanismo de add-on do kernel (A1 a A9).

Rodam o jarvis_render.py de verdade, com o tenant de exemplo, numa $HOME temporária. Só biblioteca padrão;
o render é chamado pelo `uv run`, como o bootstrap faz.

Uso: python3 test/test_addon.py   (ou python3 -m unittest test/test_addon.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "kernel"
TENANT = REPO / "tenants" / "exemplo" / "jarvis.yaml"
ADDON_EXEMPLO = REPO / "tenants" / "exemplo" / "addon-exemplo" / "addon.yaml"
ROLES = "desktop,core"
# o cache do uv fica no lugar de sempre, mesmo com a $HOME trocada (senão cada teste baixa as dependências)
UV_CACHE = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


def snapshot(home: Path) -> dict[str, str]:
    """Os arquivos que o render escreve, sem a linha de data do secrets.env."""
    out: dict[str, str] = {}
    for p in sorted(home.rglob("*")):
        if p.is_symlink():
            out[str(p.relative_to(home))] = "-> " + os.readlink(p)
        elif p.is_file() and ".cache" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="replace")
            if p.name == "secrets.env":
                text = "\n".join(line for line in text.splitlines() if "gerado_em=" not in line)
            out[str(p.relative_to(home))] = text
    return out


class AddonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="addon-test-"))
        self.user = self.tmp / "user"  # a $HOME da instalação
        self.user.mkdir()
        (self.user / ".hermes" / "skills").mkdir(parents=True)
        self.home = self.user / ".jarvis"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ apoio
    def render(self, *extra: str, home: Path | None = None, user: Path | None = None) -> subprocess.CompletedProcess:
        user = user or self.user
        env = dict(os.environ, HOME=str(user), UV_CACHE_DIR=UV_CACHE, JARVIS_IDIOMA="pt-BR")
        return subprocess.run(
            ["uv", "run", "--quiet", str(KERNEL / "bin" / "jarvis_render.py"), "--tenant", str(TENANT),
             "--role", ROLES, "--home", str(home or self.home), "--kernel", str(KERNEL), "--allow-fewer-roles", *extra],
            capture_output=True, text=True, env=env, check=False)

    def ok(self, *extra: str, **kw) -> subprocess.CompletedProcess:
        r = self.render(*extra, **kw)
        self.assertEqual(r.returncode, 0, f"render falhou:\n{r.stdout}\n{r.stderr}")
        return r

    def hermes_mcp(self) -> dict:
        return json.loads((self.home / "render" / "hermes" / "mcp.json").read_text(encoding="utf-8"))

    def addon(self, nome: str, body: str = "", *, mcp: str | None = None, skill: str | None = None) -> Path:
        """Cria um add-on mínimo em tmp/<nome>/addon.yaml (com uma skill e um MCP, se pedidos)."""
        base = self.tmp / nome
        text = f"addon: {nome}\ndescricao: add-on de teste\n"
        if mcp:
            text += textwrap.dedent(f"""\
                mcp:
                  - name: {mcp}
                    transport: stdio
                    command: python3
                    args: ["./servidor.py", "--flag", "../fora.txt"]
                """)
            write(base / "servidor.py", "print('ok')\n")
        if skill:
            text += "skills:\n  packs: [skills]\n"
            write(base / "skills" / skill / "SKILL.md", f"---\nname: {skill}\ndescription: teste\n---\n# {skill}\n")
        return write(base / "addon.yaml", text + textwrap.dedent(body))

    # ------------------------------------------------------------ A1
    def test_a1_addon_exemplo_soma_mcp_e_skill(self) -> None:
        self.assertTrue(ADDON_EXEMPLO.is_file(), "falta o add-on de exemplo em tenants/exemplo/addon-exemplo/")
        self.ok("--addon", str(ADDON_EXEMPLO))
        mcp = self.hermes_mcp()
        self.assertIn("exemplo_addon", mcp)
        # os MCPs do kernel e do tenant continuam
        self.assertIn("cerebro", mcp)
        self.assertIn("github", mcp)
        # assistente da equipe recebe o MCP do add-on; o de cliente, não
        suporte = (self.home / "render" / "hermes" / "assistentes" / "suporte" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("exemplo_addon", suporte)
        cliente = (self.home / "render" / "hermes" / "assistentes" / "cliente-exemplo" / "config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("exemplo_addon", cliente)
        # Claude Code (papel desktop)
        self.assertIn("exemplo_addon", (self.home / "render" / "claude-mcp.sh").read_text(encoding="utf-8"))
        # a skill do add-on é instalada e linkada
        self.assertTrue((self.home / "skills" / "exemplo-addon-ola" / "SKILL.md").is_file())
        self.assertTrue((self.user / ".claude" / "skills" / "exemplo-addon-ola").is_symlink())
        self.assertTrue((self.user / ".hermes" / "skills" / "exemplo-addon-ola").is_symlink())
        self.assertIn("exemplo-addon", (self.home / "render" / "summary.txt").read_text(encoding="utf-8"))

    # ------------------------------------------------------------ A2
    def test_a2_idempotente(self) -> None:
        self.ok("--addon", str(ADDON_EXEMPLO))
        first = snapshot(self.user)
        self.ok("--addon", str(ADDON_EXEMPLO))
        self.assertEqual(first, snapshot(self.user))

    # ------------------------------------------------------------ A3
    def test_a3_lembra_sem_repetir_addon(self) -> None:
        self.ok("--addon", str(ADDON_EXEMPLO))
        com = snapshot(self.user)
        self.ok()  # como o --apply: sem --addon
        self.assertIn("exemplo_addon", self.hermes_mcp())
        self.assertEqual(com, snapshot(self.user))
        estado = json.loads((self.home / "addons.json").read_text(encoding="utf-8"))
        self.assertEqual([a["addon"] for a in estado], ["exemplo-addon"])
        self.assertEqual(estado[0]["path"], str(ADDON_EXEMPLO))

    # ------------------------------------------------------------ A4
    def test_a4_remove_addon(self) -> None:
        self.ok("--addon", str(ADDON_EXEMPLO))
        self.ok("--remove-addon", "exemplo-addon")
        mcp = self.hermes_mcp()
        self.assertNotIn("exemplo_addon", mcp)
        self.assertIn("cerebro", mcp)
        self.assertNotIn("exemplo_addon", (self.home / "render" / "claude-mcp.sh").read_text(encoding="utf-8"))
        self.assertFalse((self.home / "skills" / "exemplo-addon-ola").exists())
        self.assertFalse((self.user / ".claude" / "skills" / "exemplo-addon-ola").is_symlink())
        self.assertFalse((self.user / ".hermes" / "skills" / "exemplo-addon-ola").is_symlink())
        # o resto do tenant fica
        self.assertTrue((self.home / "skills" / "exemplo-ola" / "SKILL.md").is_file())
        self.assertFalse((self.home / "addons.json").exists())
        # e o render seguinte não traz o add-on de volta
        self.ok()
        self.assertNotIn("exemplo_addon", self.hermes_mcp())

    def test_a4_remove_um_de_dois(self) -> None:
        a = self.addon("um", mcp="mcp_um", skill="skill-um")
        b = self.addon("dois", mcp="mcp_dois", skill="skill-dois")
        self.ok("--addon", str(a), "--addon", str(b))
        self.ok("--remove-addon", "um")
        mcp = self.hermes_mcp()
        self.assertNotIn("mcp_um", mcp)
        self.assertIn("mcp_dois", mcp)
        self.assertFalse((self.home / "skills" / "skill-um").exists())
        self.assertTrue((self.home / "skills" / "skill-dois").is_dir())

    def test_a4_remove_addon_desconhecido_avisa(self) -> None:
        r = self.ok("--remove-addon", "nao-existe")
        self.assertIn("nao-existe", r.stdout + r.stderr)

    # ------------------------------------------------------------ A5
    def test_a5_mcp_com_nome_do_tenant_recusa_sem_alterar(self) -> None:
        self.ok()
        antes = snapshot(self.user)
        conflito = self.addon("conflito", mcp="exemplo_ferramentas")
        r = self.render("--addon", str(conflito))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exemplo_ferramentas", r.stderr)
        self.assertIn("conflito", r.stderr)
        self.assertEqual(antes, snapshot(self.user))

    def test_a5_mcp_com_nome_reservado_do_kernel(self) -> None:
        for nome in ("cerebro", "github", "knowledge", "escritorio"):
            with self.subTest(nome=nome):
                r = self.render("--addon", str(self.addon(f"res-{nome}", mcp=nome)))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(nome, r.stderr)
                self.assertFalse(self.home.exists())

    def test_a5_dois_addons_com_mesmo_mcp(self) -> None:
        a = self.addon("um", mcp="igual")
        b = self.addon("dois", mcp="igual")
        r = self.render("--addon", str(a), "--addon", str(b))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("igual", r.stderr)
        self.assertFalse(self.home.exists())

    def test_a5_skill_com_nome_do_tenant(self) -> None:
        r = self.render("--addon", str(self.addon("sk", skill="exemplo-ola")))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exemplo-ola", r.stderr)
        self.assertFalse(self.home.exists())

    # ------------------------------------------------------------ A6
    def test_a6_segredo_do_addon(self) -> None:
        a = self.addon("seg", """\
            segredos:
              - name: SEG_ADDON_TOKEN
                title: Token do add-on
                description: só para o teste
                required: false
            """)
        self.ok("--addon", str(a))
        secrets = self.home / "secrets.env"
        self.assertIn("SEG_ADDON_TOKEN=", secrets.read_text(encoding="utf-8"))
        # a pessoa preenche; os renders seguintes, com ou sem --addon, mantêm o valor
        secrets.write_text(secrets.read_text(encoding="utf-8").replace("SEG_ADDON_TOKEN=", "SEG_ADDON_TOKEN='valor-x'"), encoding="utf-8")
        self.ok()
        self.ok("--addon", str(a))
        self.assertIn("SEG_ADDON_TOKEN='valor-x'", secrets.read_text(encoding="utf-8"))
        catalogo = json.loads((self.home / "render" / "secrets.json").read_text(encoding="utf-8"))
        self.assertIn("SEG_ADDON_TOKEN", json.dumps(catalogo))

    def test_a6_segredo_ja_declarado_no_tenant_nao_duplica(self) -> None:
        a = self.addon("dup", """\
            segredos:
              - name: EXEMPLO_TOKEN
                required: false
            """)
        self.ok("--addon", str(a))
        text = (self.home / "secrets.env").read_text(encoding="utf-8")
        self.assertEqual(text.count("\nEXEMPLO_TOKEN="), 1)

    # ------------------------------------------------------------ A7
    def test_a7_caminhos_relativos_ao_addon(self) -> None:
        a = self.addon("rel", mcp="mcp_rel", skill="skill-rel")
        # rodado de outra pasta, para o caminho não resolver por acaso a partir do diretório atual
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.ok("--addon", "rel/addon.yaml")
        finally:
            os.chdir(cwd)
        entry = self.hermes_mcp()["mcp_rel"]
        self.assertEqual(entry["args"], [str(self.tmp / "rel" / "servidor.py"), "--flag", str(self.tmp / "fora.txt")])
        self.assertTrue((self.home / "skills" / "skill-rel" / "SKILL.md").is_file())
        estado = json.loads((self.home / "addons.json").read_text(encoding="utf-8"))
        self.assertEqual(estado[0]["path"], str(a))

    # ------------------------------------------------------------ A8
    def test_a8_sem_addon_nada_muda(self) -> None:
        self.ok()
        sem = snapshot(self.user)
        self.assertFalse((self.home / "addons.json").exists())
        self.assertNotIn("add-on", (self.home / "render" / "summary.txt").read_text(encoding="utf-8"))
        # ligar e desligar um add-on volta exatamente ao mesmo resultado
        self.ok("--addon", str(ADDON_EXEMPLO))
        self.ok("--remove-addon", "exemplo-addon")
        self.assertEqual(sem, snapshot(self.user))

    # ------------------------------------------------------------ A9
    def test_a9_addon_inexistente(self) -> None:
        r = self.render("--addon", str(self.tmp / "nao" / "addon.yaml"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("addon.yaml", r.stderr)
        self.assertFalse(self.home.exists())

    def test_a9_addon_invalido(self) -> None:
        casos = {
            "yaml-quebrado": "addon: [x\n",
            "sem-nome": "descricao: falta o nome\n",
            "nome-ruim": "addon: Nome Com Espaço\n",
            "campo-desconhecido": "addon: x\ncrons: []\n",
            "mcp-sem-command": "addon: x\nmcp:\n  - name: m\n    transport: stdio\n",
            "pack-inexistente": "addon: x\nskills:\n  packs: [nao-tem]\n",
        }
        for caso, text in casos.items():
            with self.subTest(caso=caso):
                f = write(self.tmp / caso / "addon.yaml", text)
                r = self.render("--addon", str(f))
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("add-on", r.stderr)
                self.assertFalse(self.home.exists())

    def test_a9_addon_lembrado_que_sumiu(self) -> None:
        a = self.addon("some", mcp="mcp_some")
        self.ok("--addon", str(a))
        antes = snapshot(self.user)
        shutil.rmtree(a.parent)
        r = self.render()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--remove-addon some", r.stderr)
        self.assertEqual(antes, snapshot(self.user))
        # e dá para desligar mesmo sem o arquivo
        self.ok("--remove-addon", "some")
        self.assertNotIn("mcp_some", self.hermes_mcp())

    def test_a9_dry_run_nao_escreve(self) -> None:
        r = self.ok("--addon", str(ADDON_EXEMPLO), "--dry-run")
        self.assertIn("exemplo-addon", r.stdout + r.stderr)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
