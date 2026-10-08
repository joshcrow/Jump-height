"""T-H4 (spec 2026-10-07 section 8.1): audit F-25's help/dispatch parity
check, run against the real main.cpp. F-25 was three shipped commands
missing from the help line, so `jump status` reported them as absent; the
batch-2 commands (events, evstat, evclear) must not repeat it.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import importlib.machinery
import re
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
MAIN_CPP = REPO / "firmware" / "src" / "main.cpp"


def _load_jump():
    path = str(REPO / "tools" / "jump")
    loader = importlib.machinery.SourceFileLoader("jumpcli_help_parity", path)
    mod = types.ModuleType("jumpcli_help_parity")
    mod.__file__ = path
    loader.exec_module(mod)
    return mod


def _help_line(src: str) -> str:
    m = re.search(r'emitLine\("(# commands: [^"]*)"\)', src)
    assert m, "printHelp() no longer emits a '# commands:' line"
    return m.group(1)


class TestHelpDispatchParity(unittest.TestCase):
    def setUp(self) -> None:
        self.jump = _load_jump()
        self.src = MAIN_CPP.read_text()
        self.line = _help_line(self.src)

    def test_every_dispatched_command_is_in_help(self) -> None:
        self.assertEqual(self.jump.help_dispatch_gap(self.line, self.src), [])

    def test_the_batch_2_commands_are_dispatched_and_advertised(self) -> None:
        cmds = self.jump.help_commands(self.line)
        for c in ("events", "evstat", "evclear"):
            self.assertIn(c, cmds)
            self.assertIn(f'cmd == "{c}"', self.src)

    def test_the_check_still_catches_a_gap(self) -> None:
        """A parity check that cannot fail is not one (CLAUDE.md rule 3)."""
        line = self.line.replace(" | evclear", "")
        self.assertEqual(self.jump.help_dispatch_gap(line, self.src), ["evclear"])


if __name__ == "__main__":
    unittest.main()
