#!/usr/bin/env python3
"""Plugins do Hermes trazidos por add-on: cópia para o home do Hermes e para o perfil, e `plugins.enabled` (HP1–HP8).

Uso: python3 test/test_hermes_plugins.py
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent.parent / "kernel"
SCRIPT = KERNEL / "bin" / "jarvis_hermes_plugins.py"
ASSISTANT = KERNEL / "bin" / "jarvis_assistant.py"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.src = t / "addon" / "hp" / "guard"
        self.src.mkdir(parents=True)
        (self.src / "plugin.yaml").write_text("name: guard\nversion: '1.0'\n", encoding="utf-8")
        (self.src / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
        (self.src / "__pycache__").mkdir()
        (self.src / "__pycache__" / "x.pyc").write_bytes(b"\0")
        self.home = t / "hermes"
        self.home.mkdir()
        self.list = t / "plugins.json"
        self.want(["guard"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def want(self, names: list[str]) -> None:
        self.list.write_text(json.dumps([{"name": n, "path": str(self.src)} for n in names]), encoding="utf-8")

    def run_script(self, home: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["uv", "run", "--quiet", str(SCRIPT), str(self.list), str(home or self.home)],
                              capture_output=True, text=True, timeout=120)

    def config(self) -> dict:
        import re
        text = (self.home / "config.yaml").read_text(encoding="utf-8")
        m = re.search(r"enabled:\n((?:\s*- .*\n)*)", text)
        return {"text": text, "enabled": re.findall(r"- (\S+)", m.group(1)) if m else []}

    def test_hp1_copies_the_folder_and_enables_keeping_what_was_there(self) -> None:
        (self.home / "config.yaml").write_text("model:\n  default: x\nplugins:\n  enabled:\n  - mine\n  disabled:\n  - noisy\n",
                                               encoding="utf-8")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dest = self.home / "plugins" / "guard"
        self.assertTrue((dest / "plugin.yaml").is_file() and (dest / "__init__.py").is_file())
        self.assertFalse((dest / "__pycache__").exists())
        c = self.config()
        self.assertEqual(c["enabled"], ["mine", "guard"])
        self.assertIn("noisy", c["text"])
        self.assertIn("default: x", c["text"])
        self.assertEqual(oct((self.home / "config.yaml").stat().st_mode & 0o777), "0o600")

    def test_hp2_idempotent(self) -> None:
        self.run_script()
        self.assertEqual(self.run_script().returncode, 3)

    def test_hp3_changed_plugin_is_copied_again(self) -> None:
        self.run_script()
        (self.src / "__init__.py").write_text("def register(ctx):\n    return None\n", encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertIn("return None", (self.home / "plugins" / "guard" / "__init__.py").read_text(encoding="utf-8"))
        self.assertEqual(self.run_script().returncode, 3)

    def test_hp4_removed_plugin_loses_folder_and_name_only_if_the_kernel_installed_it(self) -> None:
        (self.home / "config.yaml").write_text("plugins:\n  enabled:\n  - mine\n", encoding="utf-8")
        mine = self.home / "plugins" / "mine"
        mine.mkdir(parents=True)
        (mine / "plugin.yaml").write_text("name: mine\n", encoding="utf-8")
        self.run_script()
        self.want([])
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse((self.home / "plugins" / "guard").exists())
        self.assertTrue(mine.is_dir())
        self.assertEqual(self.config()["enabled"], ["mine"])
        self.assertFalse((self.home / ".whitebeard-plugins").exists())
        self.assertEqual(self.run_script().returncode, 3)

    def test_hp5_folder_the_kernel_did_not_install_is_not_overwritten(self) -> None:
        hand = self.home / "plugins" / "guard"
        hand.mkdir(parents=True)
        (hand / "__init__.py").write_text("# feito à mão\n", encoding="utf-8")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("guard", r.stdout)
        self.assertEqual((hand / "__init__.py").read_text(encoding="utf-8"), "# feito à mão\n")

    def test_hp6_nothing_declared_and_nothing_installed_touches_nothing(self) -> None:
        self.want([])
        self.assertEqual(self.run_script().returncode, 3)
        self.assertFalse((self.home / "config.yaml").exists())
        self.assertFalse((self.home / "plugins").exists())

    def test_hp7_missing_home_is_not_an_error(self) -> None:
        self.assertEqual(self.run_script(self.home / "absent").returncode, 3)

    def test_hp8_assistant_profile_gets_the_plugin_and_counts_as_changed(self) -> None:
        t = Path(self._tmp.name)
        jarvis, render, profile = t / "jarvis", t / "render", t / "profiles" / "helper"
        (jarvis / "render" / "hermes").mkdir(parents=True)
        (jarvis / "skills").mkdir()
        render.mkdir()
        profile.mkdir(parents=True)
        (render / "config.yaml").write_text('{"model": {"default": "x"}}', encoding="utf-8")
        (render / "env").write_text("A=1\n", encoding="utf-8")
        (render / "SOUL.md").write_text("persona\n", encoding="utf-8")
        (render / "skills.txt").write_text("", encoding="utf-8")

        def apply() -> subprocess.CompletedProcess:
            return subprocess.run(["uv", "run", "--quiet", str(ASSISTANT), str(render), str(profile)], capture_output=True,
                                  text=True, env={**os.environ, "JARVIS_HOME": str(jarvis)}, timeout=120)
        self.assertEqual(apply().returncode, 0)
        self.assertEqual(apply().returncode, 3)  # sem plugins.json: nada a fazer
        (jarvis / "render" / "hermes" / "plugins.json").write_text(self.list.read_text(encoding="utf-8"), encoding="utf-8")
        r = apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # mudou: o bootstrap reinicia o gateway
        self.assertIn("plugins", r.stdout)
        self.assertTrue((profile / "plugins" / "guard" / "__init__.py").is_file())
        self.assertIn("guard", (profile / "config.yaml").read_text(encoding="utf-8"))
        self.assertIn("default: x", (profile / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(apply().returncode, 3)
        (jarvis / "render" / "hermes" / "plugins.json").unlink()  # o add-on saiu: o render apaga a lista
        self.assertEqual(apply().returncode, 0)
        self.assertFalse((profile / "plugins" / "guard").exists())


if __name__ == "__main__":
    unittest.main()
