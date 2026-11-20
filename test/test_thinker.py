"""Quem pensa: o status consulta os logins sem travar a página (primeiro status numa instalação nova)."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

KERNEL_BIN = Path(__file__).resolve().parent.parent / "kernel" / "bin"
sys.path.insert(0, str(KERNEL_BIN))

import jarvis_thinker as th  # noqa: E402


def fake_hermes(folder: Path, delay: float, logged_in: bool = True) -> None:
    """Um `hermes` que demora `delay` segundos em cada `auth status` / `auth list`."""
    exe = folder / "hermes"
    exe.write_text(
        "#!/usr/bin/env python3\nimport sys, time\n"
        f"time.sleep({delay})\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['auth', 'status']:\n"
        f"    print(args[2] + (': logged in' if {logged_in} else ': logged out'))\n"
        "elif args[:2] == ['auth', 'list']:\n"
        f"    print('  #1  whitebeard-os   api_key id=1 priority=0 manual' if {logged_in} else '')\n",
        encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)


class ThinkerStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "jarvis"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.old_path}"
        self.kernel = KERNEL_BIN.parent

    def tearDown(self) -> None:
        os.environ["PATH"] = self.old_path
        self._tmp.cleanup()

    def test_th1_logins_checked_in_parallel(self) -> None:
        """Três provedores, 3 s cada: em série seriam 9 s; o status sai bem antes."""
        fake_hermes(self.bin, 3)
        t0 = time.monotonic()
        s = th.status(self.home, self.kernel)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 6, f"status levou {elapsed:.1f} s")
        self.assertEqual(s["logged_in"], {"claude": True, "chatgpt": True, "openrouter": True})

    def test_th2_slow_hermes_does_not_hang_status(self) -> None:
        """Hermes travado: o status responde dentro do limite e não grava a resposta que não veio."""
        fake_hermes(self.bin, 60)
        t0 = time.monotonic()
        s = th.status(self.home, self.kernel)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, th.CHECK_TIMEOUT_S + 4, f"status levou {elapsed:.1f} s")
        self.assertEqual(s["logged_in"], {"claude": False, "chatgpt": False, "openrouter": False})
        cache = json.loads((self.home / "thinker.cache.json").read_text()) if (self.home / "thinker.cache.json").exists() else {}
        self.assertEqual(cache, {}, "um tempo esgotado não pode virar resposta guardada")

    def test_th3_timeout_keeps_last_known_answer(self) -> None:
        """Com resposta antiga no cache (vencida), um tempo esgotado devolve a antiga em vez de 'desconectado'."""
        (self.home / "thinker.cache.json").write_text(json.dumps(
            {p: {"logged_in": True, "at": time.time() - 3600} for p in th.PROVIDERS}))
        fake_hermes(self.bin, 60)
        s = th.status(self.home, self.kernel)
        self.assertEqual(s["logged_in"], {"claude": True, "chatgpt": True, "openrouter": True})

    def test_th4_fresh_cache_skips_hermes(self) -> None:
        (self.home / "thinker.cache.json").write_text(json.dumps(
            {p: {"logged_in": p == "claude", "at": time.time()} for p in th.PROVIDERS}))
        fake_hermes(self.bin, 60)
        t0 = time.monotonic()
        s = th.status(self.home, self.kernel)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(s["logged_in"], {"claude": True, "chatgpt": False, "openrouter": False})


if __name__ == "__main__":
    unittest.main()
