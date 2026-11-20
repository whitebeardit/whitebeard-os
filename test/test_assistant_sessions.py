"""Assistente: quando o apply muda as skills (lista ou conteúdo), os MCPs ou o SOUL, as conversas abertas são
encerradas (como um /reset em cada canal), para a próxima mensagem já usar o perfil novo (S1–S6)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent.parent / "kernel"
SCRIPT = KERNEL / "bin" / "jarvis_assistant.py"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.home = t / "jarvis"
        self.render = t / "render"
        self.profile = t / "profiles" / "pichi"
        (self.home / "skills" / "grading").mkdir(parents=True)
        (self.home / "skills" / "grading" / "SKILL.md").write_text("---\nname: grading\n---\nv1\n", encoding="utf-8")
        self.render.mkdir()
        self.profile.mkdir(parents=True)
        self.write_render(mcp={"vault": {"command": "python3"}})
        db = sqlite3.connect(self.profile / "state.db")
        db.execute("create table sessions (id text primary key, source text, started_at real, ended_at real, "
                   "end_reason text)")
        now = time.time()
        db.executemany("insert into sessions values (?, ?, ?, ?, ?)", [
            ("open-discord", "discord", now - 100, None, None),
            ("open-telegram", "telegram", now - 50, None, None),
            ("ended", "discord", now - 900, now - 800, "session_reset"),
            ("open-cli", "cli", now - 10, None, None),  # sessão de terminal (Paperclip, cron): não é conversa de canal
        ])
        db.commit()
        db.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_render(self, mcp: dict, skills: tuple = ("grading",), soul: str = "Você é o pichi.\n") -> None:
        import json
        cfg = {"model": {"default": "claude"}, "mcp_servers": mcp}
        (self.render / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")  # JSON é YAML válido
        (self.render / "env").write_text("DISCORD_BOT_TOKEN=x\n", encoding="utf-8")
        (self.render / "SOUL.md").write_text(soul, encoding="utf-8")
        (self.render / "skills.txt").write_text("\n".join(skills) + "\n", encoding="utf-8")

    def apply(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "JARVIS_HOME": str(self.home)}
        return subprocess.run(["uv", "run", "--quiet", str(SCRIPT), str(self.render), str(self.profile)],
                              capture_output=True, text=True, env=env, timeout=120)

    def sessions(self) -> dict[str, str | None]:
        db = sqlite3.connect(self.profile / "state.db")
        try:
            return dict(db.execute("select id, end_reason from sessions"))
        finally:
            db.close()

    OPEN = {"open-discord": None, "open-telegram": None, "ended": "session_reset", "open-cli": None}


class SessionResetTest(Case):
    def test_s1_first_apply_keeps_conversations(self) -> None:
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sessions(), self.OPEN)

    def test_s2_nothing_changed(self) -> None:
        self.apply()
        r = self.apply()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(self.sessions(), self.OPEN)

    def test_s3_skill_content_changed_ends_channel_conversations(self) -> None:
        self.apply()
        (self.home / "skills" / "grading" / "SKILL.md").write_text("---\nname: grading\n---\nv2\n", encoding="utf-8")
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)  # mudou: o bootstrap reinicia o gateway
        self.assertEqual(self.sessions(), {"open-discord": "session_reset", "open-telegram": "session_reset",
                                           "ended": "session_reset", "open-cli": None})
        self.assertIn("2 conversa", r.stdout)

    def test_s4_mcp_changed(self) -> None:
        self.apply()
        self.write_render(mcp={"vault": {"command": "python3"}, "mastery": {"command": "python3"}})
        self.apply()
        self.assertEqual(self.sessions()["open-discord"], "session_reset")

    def test_s5_skill_list_changed(self) -> None:
        self.apply()
        self.write_render(mcp={"vault": {"command": "python3"}}, skills=())
        self.apply()
        self.assertEqual(self.sessions()["open-telegram"], "session_reset")

    def test_s6_without_state_db(self) -> None:
        (self.profile / "state.db").unlink()
        self.apply()
        (self.home / "skills" / "grading" / "SKILL.md").write_text("v3\n", encoding="utf-8")
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
