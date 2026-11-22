#!/usr/bin/env python3
"""Testes de aceite do mecanismo de add-on do kernel (A1 a A10).

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
TENANT = REPO / "tenants" / "example" / "jarvis.yaml"
EXAMPLE_ADDON = REPO / "tenants" / "example" / "addon-example" / "addon.yaml"
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
        env = dict(os.environ, HOME=str(user), UV_CACHE_DIR=UV_CACHE, JARVIS_LANGUAGE="pt-BR")
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

    def addon(self, name: str, body: str = "", *, mcp: str | None = None, skill: str | None = None) -> Path:
        """Cria um add-on mínimo em tmp/<nome>/addon.yaml (com uma skill e um MCP, se pedidos)."""
        base = self.tmp / name
        text = f"addon: {name}\ndescription: add-on de teste\n"
        if mcp:
            text += textwrap.dedent(f"""\
                mcp:
                  - name: {mcp}
                    transport: stdio
                    command: python3
                    args: ["./server.py", "--flag", "../outside.txt"]
                """)
            write(base / "server.py", "print('ok')\n")
        if skill:
            text += "skills:\n  packs: [skills]\n"
            write(base / "skills" / skill / "SKILL.md", f"---\nname: {skill}\ndescription: teste\n---\n# {skill}\n")
        return write(base / "addon.yaml", text + textwrap.dedent(body))

    # ------------------------------------------------------------ A1
    def test_a1_example_addon_adds_mcp_and_skill(self) -> None:
        self.assertTrue(EXAMPLE_ADDON.is_file(), "falta o add-on de exemplo em tenants/example/addon-example/")
        self.ok("--addon", str(EXAMPLE_ADDON))
        mcp = self.hermes_mcp()
        self.assertIn("example_addon", mcp)
        # os MCPs do kernel e do tenant continuam
        self.assertIn("vault", mcp)
        self.assertIn("github", mcp)
        # assistente da equipe recebe o MCP do add-on; o de cliente, não
        support = (self.home / "render" / "hermes" / "assistants" / "support" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("example_addon", support)
        client = (self.home / "render" / "hermes" / "assistants" / "example-client" / "config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("example_addon", client)
        # Claude Code (papel desktop)
        self.assertIn("example_addon", (self.home / "render" / "claude-mcp.sh").read_text(encoding="utf-8"))
        # a skill do add-on é instalada e linkada
        self.assertTrue((self.home / "skills" / "example-addon-hello" / "SKILL.md").is_file())
        self.assertTrue((self.user / ".claude" / "skills" / "example-addon-hello").is_symlink())
        self.assertTrue((self.user / ".hermes" / "skills" / "example-addon-hello").is_symlink())
        self.assertIn("example-addon", (self.home / "render" / "summary.txt").read_text(encoding="utf-8"))

    def test_a1_addon_mcp_gets_tenant_and_data(self) -> None:
        """ADR-009 do Mastery: o tenant chega ao MCP pelo ambiente, nunca como parâmetro de ferramenta."""
        a = self.addon("env", """\
            mcp:
              - name: mcp_env
                transport: stdio
                command: python3
                args: ["x.py"]
                env:
                  JARVIS_TENANT: "other"
            """)
        self.ok("--addon", str(a))
        env = self.hermes_mcp()["mcp_env"]["env"]
        self.assertEqual(env["JARVIS_TENANT"], "example")  # o add-on não escolhe o tenant
        self.assertEqual(env["JARVIS_DATA"], str(self.user / ".example"))
        # MCP do tenant não muda
        self.assertNotIn("JARVIS_TENANT", self.hermes_mcp().get("example_tools", {}).get("env", {}))

    # ------------------------------------------------------------ A2
    def test_a2_idempotent(self) -> None:
        self.ok("--addon", str(EXAMPLE_ADDON))
        first = snapshot(self.user)
        self.ok("--addon", str(EXAMPLE_ADDON))
        self.assertEqual(first, snapshot(self.user))

    # ------------------------------------------------------------ A3
    def test_a3_remembers_without_repeating_addon(self) -> None:
        self.ok("--addon", str(EXAMPLE_ADDON))
        with_addon = snapshot(self.user)
        self.ok()  # como o --apply: sem --addon
        self.assertIn("example_addon", self.hermes_mcp())
        self.assertEqual(with_addon, snapshot(self.user))
        state = json.loads((self.home / "addons.json").read_text(encoding="utf-8"))
        self.assertEqual([a["addon"] for a in state], ["example-addon"])
        self.assertEqual(state[0]["path"], str(EXAMPLE_ADDON))

    # ------------------------------------------------------------ A4
    def test_a4_remove_addon(self) -> None:
        self.ok("--addon", str(EXAMPLE_ADDON))
        self.ok("--remove-addon", "example-addon")
        mcp = self.hermes_mcp()
        self.assertNotIn("example_addon", mcp)
        self.assertIn("vault", mcp)
        self.assertNotIn("example_addon", (self.home / "render" / "claude-mcp.sh").read_text(encoding="utf-8"))
        self.assertFalse((self.home / "skills" / "example-addon-hello").exists())
        self.assertFalse((self.user / ".claude" / "skills" / "example-addon-hello").is_symlink())
        self.assertFalse((self.user / ".hermes" / "skills" / "example-addon-hello").is_symlink())
        # o resto do tenant fica
        self.assertTrue((self.home / "skills" / "example-hello" / "SKILL.md").is_file())
        self.assertFalse((self.home / "addons.json").exists())
        # e o render seguinte não traz o add-on de volta
        self.ok()
        self.assertNotIn("example_addon", self.hermes_mcp())

    def test_a4_remove_one_of_two(self) -> None:
        a = self.addon("one", mcp="mcp_one", skill="skill-one")
        b = self.addon("two", mcp="mcp_two", skill="skill-two")
        self.ok("--addon", str(a), "--addon", str(b))
        self.ok("--remove-addon", "one")
        mcp = self.hermes_mcp()
        self.assertNotIn("mcp_one", mcp)
        self.assertIn("mcp_two", mcp)
        self.assertFalse((self.home / "skills" / "skill-one").exists())
        self.assertTrue((self.home / "skills" / "skill-two").is_dir())

    def test_a4_remove_unknown_addon_warns(self) -> None:
        r = self.ok("--remove-addon", "does-not-exist")
        self.assertIn("does-not-exist", r.stdout + r.stderr)

    # ------------------------------------------------------------ A5
    def test_a5_mcp_with_tenant_name_refused_without_changes(self) -> None:
        self.ok()
        before = snapshot(self.user)
        conflict = self.addon("conflict", mcp="example_tools")
        r = self.render("--addon", str(conflict))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("example_tools", r.stderr)
        self.assertIn("conflict", r.stderr)
        self.assertEqual(before, snapshot(self.user))

    def test_a5_mcp_with_reserved_kernel_name(self) -> None:
        for name in ("vault", "github", "knowledge", "office"):
            with self.subTest(name=name):
                r = self.render("--addon", str(self.addon(f"res-{name}", mcp=name)))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(name, r.stderr)
                self.assertFalse(self.home.exists())

    def test_a5_two_addons_with_same_mcp(self) -> None:
        a = self.addon("one", mcp="same")
        b = self.addon("two", mcp="same")
        r = self.render("--addon", str(a), "--addon", str(b))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("same", r.stderr)
        self.assertFalse(self.home.exists())

    def test_a5_skill_with_tenant_name(self) -> None:
        r = self.render("--addon", str(self.addon("sk", skill="example-hello")))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("example-hello", r.stderr)
        self.assertFalse(self.home.exists())

    # ------------------------------------------------------------ A6
    def test_a6_addon_secret(self) -> None:
        a = self.addon("sec", """\
            secrets:
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
        catalog = json.loads((self.home / "render" / "secrets.json").read_text(encoding="utf-8"))
        self.assertIn("SEG_ADDON_TOKEN", json.dumps(catalog))

    def test_a6_secret_declared_in_tenant_not_duplicated(self) -> None:
        a = self.addon("dup", """\
            secrets:
              - name: EXAMPLE_TOKEN
                required: false
            """)
        self.ok("--addon", str(a))
        text = (self.home / "secrets.env").read_text(encoding="utf-8")
        self.assertEqual(text.count("\nEXAMPLE_TOKEN="), 1)

    # ------------------------------------------------------------ A7
    def test_a7_paths_relative_to_addon(self) -> None:
        a = self.addon("rel", mcp="mcp_rel", skill="skill-rel")
        # rodado de outra pasta, para o caminho não resolver por acaso a partir do diretório atual
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.ok("--addon", "rel/addon.yaml")
        finally:
            os.chdir(cwd)
        entry = self.hermes_mcp()["mcp_rel"]
        self.assertEqual(entry["args"], [str(self.tmp / "rel" / "server.py"), "--flag", str(self.tmp / "outside.txt")])
        self.assertTrue((self.home / "skills" / "skill-rel" / "SKILL.md").is_file())
        state = json.loads((self.home / "addons.json").read_text(encoding="utf-8"))
        self.assertEqual(state[0]["path"], str(a))

    # ------------------------------------------------------------ A8
    def test_a8_without_addon_nothing_changes(self) -> None:
        self.ok()
        without = snapshot(self.user)
        self.assertFalse((self.home / "addons.json").exists())
        self.assertNotIn("add-on", (self.home / "render" / "summary.txt").read_text(encoding="utf-8"))
        # ligar e desligar um add-on volta exatamente ao mesmo resultado
        self.ok("--addon", str(EXAMPLE_ADDON))
        self.ok("--remove-addon", "example-addon")
        self.assertEqual(without, snapshot(self.user))

    # ------------------------------------------------------------ A9
    def test_a9_missing_addon(self) -> None:
        r = self.render("--addon", str(self.tmp / "missing" / "addon.yaml"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("addon.yaml", r.stderr)
        self.assertFalse(self.home.exists())

    def test_a9_invalid_addon(self) -> None:
        cases = {
            "broken-yaml": "addon: [x\n",
            "no-name": "description: falta o nome\n",
            "bad-name": "addon: Nome Com Espaço\n",
            "unknown-field": "addon: x\ncrons: []\n",
            "mcp-without-command": "addon: x\nmcp:\n  - name: m\n    transport: stdio\n",
            "missing-pack": "addon: x\nskills:\n  packs: [missing]\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                f = write(self.tmp / case / "addon.yaml", text)
                r = self.render("--addon", str(f))
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("add-on", r.stderr)
                self.assertFalse(self.home.exists())

    def test_a9_remembered_addon_that_vanished(self) -> None:
        a = self.addon("gone", mcp="mcp_gone")
        self.ok("--addon", str(a))
        before = snapshot(self.user)
        shutil.rmtree(a.parent)
        r = self.render()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--remove-addon gone", r.stderr)
        self.assertEqual(before, snapshot(self.user))
        # e dá para desligar mesmo sem o arquivo
        self.ok("--remove-addon", "gone")
        self.assertNotIn("mcp_gone", self.hermes_mcp())

    # ------------------------------------------------------------ A10: plugins do Hermes
    def plugin_addon(self, name: str, plugin: str, *, files: bool = True) -> Path:
        base = self.tmp / name
        if files:
            write(base / "hp" / plugin / "plugin.yaml", f"name: {plugin}\nversion: '1.0'\n")
            write(base / "hp" / plugin / "__init__.py", "def register(ctx):\n    pass\n")
        return self.addon(name, f"hermes:\n  plugins: [hp/{plugin}]\n")

    def hermes_plugins(self) -> list:
        return json.loads((self.home / "render" / "hermes" / "plugins.json").read_text(encoding="utf-8"))

    def test_a10_example_addon_lists_its_hermes_plugin(self) -> None:
        self.ok("--addon", str(EXAMPLE_ADDON))
        self.assertEqual(self.hermes_plugins(), [{"name": "example-addon-note",
                                                  "path": str(EXAMPLE_ADDON.parent / "hermes-plugin" / "example-addon-note")}])
        self.ok()  # lembrado
        self.assertEqual(len(self.hermes_plugins()), 1)

    def test_a10_removed_addon_takes_the_list_away(self) -> None:
        self.ok("--addon", str(EXAMPLE_ADDON))
        self.ok("--remove-addon", "example-addon")
        # sem a lista, o bootstrap tira do Hermes o que tinha instalado (HP4 e HP8)
        self.assertFalse((self.home / "render" / "hermes" / "plugins.json").exists())

    def test_a10_without_plugins_no_file(self) -> None:
        self.ok("--addon", str(self.addon("plain", mcp="mcp_plain")))
        self.assertFalse((self.home / "render" / "hermes" / "plugins.json").exists())

    def test_a10_same_plugin_name_in_two_addons_refused_without_changes(self) -> None:
        a = self.plugin_addon("one", "shared-plugin")
        b = self.plugin_addon("two", "shared-plugin")
        self.ok("--addon", str(a))
        before = snapshot(self.home)
        r = self.render("--addon", str(b))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shared-plugin", r.stdout + r.stderr)
        self.assertEqual(snapshot(self.home), before)

    def test_a10_plugin_folder_without_manifest_refused(self) -> None:
        r = self.render("--addon", str(self.plugin_addon("broken", "missing", files=False)))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("plugin.yaml", r.stdout + r.stderr)
        self.assertFalse((self.home / "addons.json").exists())

    def test_a9_dry_run_writes_nothing(self) -> None:
        r = self.ok("--addon", str(EXAMPLE_ADDON), "--dry-run")
        self.assertIn("example-addon", r.stdout + r.stderr)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
