"""Rotina que publica no grupo de um assistente (RP1–RP7): no grupo sai só o texto final, no canal escolhido."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

KERNEL_BIN = Path(__file__).resolve().parent.parent / "kernel" / "bin"
sys.path.insert(0, str(KERNEL_BIN))

import jarvis_courier as courier  # noqa: E402
import jarvis_routines as routines  # noqa: E402

ROUTINE = "\n\n[origin: assistant:tutor]\n[routine: monday]"


def task(status: str, desc: str) -> dict:
    return {"id": "abc", "identifier": "T-1", "title": "Recado", "status": status, "description": "Escreva." + desc}


class PublicationTest(unittest.TestCase):
    def test_rp1_group_gets_only_the_final_text(self) -> None:
        got = courier.message(task("done", ROUTINE), "DELIVERY: Bom dia, turma!\nTudo certo?\nArquivo: deliveries/x.md")
        self.assertEqual(got, "Bom dia, turma!\nTudo certo?")

    def test_rp2_owner_keeps_header_and_file(self) -> None:
        for desc in ("\n\n[origin: telegram]\n[routine: monday]", "\n\n[origin: assistant:tutor]", ""):
            got = courier.message(task("done", desc), "DELIVERY: Feito.\nArquivo: deliveries/x.md")
            self.assertEqual(got, "✅ Entregue: Recado (T-1)\nFeito.\nArquivo: deliveries/x.md")

    def test_rp3_pending_and_cancelled_of_a_publishing_routine_go_to_the_owner(self) -> None:
        self.assertEqual(courier.destination_of(task("done", ROUTINE)), "assistant:tutor")
        self.assertEqual(courier.destination_of(task("blocked", ROUTINE)), "telegram")
        self.assertEqual(courier.destination_of(task("cancelled", ROUTINE)), "telegram")
        self.assertEqual(courier.destination_of(task("blocked", "\n\n[origin: assistant:tutor]")), "assistant:tutor")
        self.assertIn("Cancelada", courier.message(task("cancelled", ROUTINE), "REJECTED: não deu"))

    def test_rp4_delivery_without_text_publishes_nothing(self) -> None:
        self.assertEqual(courier.message(task("done", ROUTINE), "DELIVERY: \nArquivo: deliveries/x.md"), "")

    def home(self, channels: str) -> Path:
        home = Path(tempfile.mkdtemp())
        (home / "render" / "hermes").mkdir(parents=True)
        (home / "render" / "hermes" / "assistants.json").write_text(json.dumps([{"name": "tutor", "channel": "discord"}]))
        profile = home / "hermes" / "profiles" / "tutor"
        profile.mkdir(parents=True)
        (profile / ".env").write_text(f"DISCORD_ALLOWED_CHANNELS={channels}\n")
        (home / "profile.env").write_text(f"HERMES_HOME={home / 'hermes'}\n")
        return home

    def test_rp5_chosen_channel_must_be_one_of_the_assistant(self) -> None:
        home = self.home("111111,222222")
        self.assertEqual(courier.assistant_destination(home, "tutor"), ("tutor", "discord:111111"))
        self.assertEqual(courier.assistant_destination(home, "tutor:222222"), ("tutor", "discord:222222"))
        self.assertIsNone(courier.assistant_destination(home, "tutor:333333"))
        self.assertIsNone(courier.assistant_destination(home, "nobody:111111"))

    def test_rp6_where_accepts_a_channel_id(self) -> None:
        base = {"name": "monday", "title": "Recado", "does": "Escreva.", "when": "0 9 * * 1"}
        for ok in ("telegram", "assistant:tutor", "assistant:tutor:123456789", "assistant:tutor:-1001234567"):
            self.assertIsNone(routines.validate_routine({**base, "where": ok}, []), ok)
        for bad in ("assistant:tutor:", "assistant:tutor:abc", "assistant:tutor:12 34", "discord:123", "assistant:Tutor"):
            self.assertIsNotNone(routines.validate_routine({**base, "where": bad}, []), bad)

    def test_rp7_task_of_a_publishing_routine_tells_the_role(self) -> None:
        made = []

        def api(method, path, body=None):
            if path == "/companies":
                return [{"id": "c1"}]
            made.append(body)
            return {"id": "i1", "identifier": "T-1", "title": body["title"]}

        old = routines.api, routines.position_ids
        routines.api, routines.position_ids = api, lambda home: {"work": {"id": "p1"}}
        try:
            base = {"name": "monday", "title": "Recado", "does": "Escreva.", "position": "work"}
            routines.fire(Path("."), {**base, "where": "assistant:tutor:123456"})
            routines.fire(Path("."), {**base, "where": "telegram"})
        finally:
            routines.api, routines.position_ids = old
        self.assertIn("PUBLICAÇÃO:", made[0]["description"])
        self.assertIn("[origin: assistant:tutor:123456]", made[0]["description"])
        self.assertTrue(courier.publishes({"description": made[0]["description"]}))
        self.assertNotIn("PUBLICAÇÃO:", made[1]["description"])


if __name__ == "__main__":
    unittest.main()
