"""The fee split in the copy must keep its direction (30 percent platform, 70 percent creator) and links must be
only https links on PluginPad hosts, in every spelling a reader or a browser would follow.

Each refusal runs the real CLI on a copy of the committed plugin against the committed tool snapshot, and sits next to
the healthy copy, which exits 0.
Run: python3 -m unittest discover -s tests -v
"""

import pathlib
import subprocess
import sys
import unittest

from test_check_plugin import ROOT, SNAPSHOT, Base

SKILL = "plugins/pluginpad/skills/launch-token/SKILL.md"
README = "plugins/pluginpad/README.md"
SKILL_SPLIT = "30 percent of the token's creator fees for PluginPad and 70 percent for the creator"
README_SPLIT = "30 percent of the token's creator fees for PluginPad, 70 percent for the creator"
BPS = "(`platformBps` 3000 is 30 percent, `creatorBps` 7000 is 70 percent)"


class CliBase(Base):
    def cli(self, real):
        cmd = [sys.executable, str(ROOT / "scripts" / "check_plugin.py"), "--root", str(real), "--tools-file", str(SNAPSHOT)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        return done.returncode, done.stdout

    def edited(self, rel, old, new):
        real = self.real_copy()
        path = real / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")
        return real

    def append(self, rel, line):
        real = self.real_copy()
        path = real / rel
        path.write_text(path.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")
        return real

    def refused(self, real, needle=None):
        code, out = self.cli(real)
        self.assertEqual(code, 1, out)
        if needle:
            self.assertIn(needle, out)


class FeeDirection(CliBase):
    def test_the_healthy_copy_exits_0(self):
        code, out = self.cli(self.real_copy())
        self.assertEqual((code, out.splitlines()[-1]), (0, "CHECK PASS"), out)

    def test_a_swap_in_the_skill_is_refused(self):
        self.refused(self.edited(SKILL, SKILL_SPLIT, "70 percent of the token's creator fees for PluginPad and 30 percent for the creator"), "fee")

    def test_a_swap_in_the_plugin_readme_is_refused(self):
        self.refused(self.edited(README, README_SPLIT, "70 percent of the token's creator fees for PluginPad, 30 percent for the creator"), "fee")

    def test_swapped_values_behind_the_field_names_are_refused(self):
        self.refused(self.edited(SKILL, BPS, "(`platformBps` 7000 is 70 percent, `creatorBps` 3000 is 30 percent)"), "platformBps")

    def test_a_field_name_without_its_value_is_refused(self):
        self.refused(self.edited(SKILL, BPS, "(`platformBps` is 30 percent, `creatorBps` 7000 is 70 percent)"), "platformBps")

    def test_a_percentage_with_the_wrong_party_in_another_sentence_is_refused(self):
        self.refused(self.append(README, "The creator also gets 70 percent of trading volume."), "fee")

    def test_a_percentage_with_no_party_is_refused(self):
        self.refused(self.append(README, "Of that, 30 percent is kept."), "fee")

    def test_a_correct_second_statement_passes(self):
        code, out = self.cli(self.append(README, "As a reminder: 70 percent for the creator and 30 percent for PluginPad."))
        self.assertEqual(code, 0, out)


class OtherLinkForms(CliBase):
    FORMS = {
        "backslash scheme": "See https:\\\\evil.example/x for details.",
        "scheme relative": "See //evil.example/x for details.",
        "www host": "See www.evil.example for details.",
        "mailto": "Write to mailto:someone@evil.example for details.",
        "ftp": "See ftp://evil.example/x for details.",
        "javascript": "Click javascript:alert(1) for details.",
        "upper case mailto": "Write to MAILTO:someone@evil.example for details.",
    }

    def test_each_form_is_refused_in_a_readme(self):
        for name, line in self.FORMS.items():
            with self.subTest(form=name):
                self.refused(self.append(README, line), "link")

    def test_each_form_is_refused_in_the_skill(self):
        for name, line in self.FORMS.items():
            with self.subTest(form=name):
                self.refused(self.append(SKILL, line), "link")

    def test_https_links_on_pluginpad_hosts_and_plain_paths_still_pass(self):
        code, out = self.cli(self.append(README, "See https://pluginpad.app/x, a/b paths, contact@pluginpad.app and ./plugins/pluginpad."))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
