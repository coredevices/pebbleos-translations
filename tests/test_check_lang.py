# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lang_check
import lang_commands as commands
import polib
from generate_codepoint_requirements import generate_codepoint_requirements
from lang_check import check_lang, format_report
from pack_format import FONT_SLOTS

ROOT = Path(__file__).resolve().parents[1]


class CheckLanguageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "test"
        self.source.mkdir()
        self.root_patch = patch.object(commands, "LANG_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.mapping = commands.new_map("test")
        self.catalog = polib.POFile()
        self.catalog.metadata = {
            "Project-Id-Version": "test 1",
            "Report-Msgid-Bugs-To": "test@example.com",
            "POT-Creation-Date": "2026-09-15 00:00+0000",
            "PO-Revision-Date": "2026-09-15 00:00+0000",
            "Last-Translator": "Test",
            "Language-Team": "Test",
            "Language": "fr",
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": "nplurals=2; plural=(n > 1);",
        }

    def save(self):
        (self.source / commands.LANG_MAP).write_text(json.dumps(self.mapping))
        self.catalog.save(str(self.source / commands.CATALOG))

    def font(self, **options):
        entry = self.mapping["fonts"][0]
        entry.update({"file": str(ROOT / "en_IL/Heebo-Regular.ttf"), **options})
        return entry

    def test_progress_and_no_mutations(self):
        self.catalog.extend(
            [
                polib.POEntry(msgid="Hello", msgstr="Bonjour"),
                polib.POEntry(msgid="Review", msgstr="Revoir", flags=["fuzzy"]),
                polib.POEntry(msgid="Empty", msgstr=""),
                polib.POEntry(msgid="Old", msgstr="Vieux", obsolete=True),
                polib.POEntry(
                    msgid="One",
                    msgid_plural="Many",
                    msgstr_plural={0: "Un", 1: "Plusieurs"},
                ),
            ]
        )
        self.save()
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        report = check_lang("test")
        self.assertTrue(report["ok"], report)
        self.assertEqual(
            report["progress"],
            {"total": 4, "translated": 2, "needs_review": 1, "untranslated": 1},
        )
        self.assertEqual(report["built_in_coverage"], "checked")
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.source.iterdir()}
        )
        with tempfile.TemporaryDirectory() as output:
            pack = commands.pack_lang("test", output)
            self.assertEqual(report["pack_size_bytes"], pack.stat().st_size)

    def test_format_and_plural_errors(self):
        for entry in (
            polib.POEntry(msgid="Count: %d", msgstr="Compte: %s", flags=["c-format"]),
            polib.POEntry(msgid="One", msgid_plural="Many", msgstr_plural={0: "Un"}),
        ):
            with self.subTest(entry=entry.msgid):
                self.catalog[:] = [entry]
                self.save()
                report = check_lang("test")
                self.assertFalse(report["ok"])
                self.assertIn("catalog_invalid", [i["code"] for i in report["issues"]])

    def test_unicode_multiline_plural_context_and_ignored_entries(self):
        self.catalog.wrapwidth = 18
        self.catalog.extend(
            [
                polib.POEntry(msgid="Text", msgctxt="menu", msgstr='A é אב\n"ג"'),
                polib.POEntry(
                    msgid="One", msgid_plural="Many", msgstr_plural={0: "ד", 1: "ה"}
                ),
                polib.POEntry(msgid="Review", msgstr="Ж", flags=["fuzzy"]),
                polib.POEntry(msgid="Old", msgstr="Я", obsolete=True),
            ]
        )
        self.save()
        requirements = generate_codepoint_requirements(self.source / commands.CATALOG)
        self.assertTrue(set(map(ord, "Aéאבגדה")) <= set(requirements["codepoints"]))
        self.assertEqual(requirements["baseline_locale"], "fr")
        self.font()
        self.save()
        report = check_lang("test")
        self.assertTrue(report["ok"], report)
        font = report["fonts"][0]
        self.assertFalse(
            any(d["character"] in "אבגדה" for d in font["missing_characters"])
        )
        self.assertEqual(font["uncovered_characters"], [])
        base_missing = {d["character"]: d for d in font["built_in_missing_characters"]}
        self.assertNotIn("A", base_missing)
        self.assertEqual(base_missing["א"]["example"]["context"], "menu")
        self.assertNotIn("Ж", json.dumps(font, ensure_ascii=False))

    def test_required_characters_override_legacy_filters(self):
        self.font(characterRegex="א")
        self.catalog.append(polib.POEntry(msgid="Test", msgstr="אב漢"))
        self.save()
        report = check_lang("test")
        self.assertTrue(report["ok"], report)
        font = report["fonts"][0]
        self.assertIn("漢", [d["character"] for d in font["missing_characters"]])
        self.assertNotIn("ב", [d["character"] for d in font["uncovered_characters"]])
        self.assertIn("U+6F22", format_report(report))

    def test_empty_hebrew_still_requires_alphabet_and_builds_it(self):
        self.mapping["strings"]["lang"] = "he"
        self.font()
        self.save()
        report = check_lang("test")
        self.assertEqual(report["character_requirements"]["baseline_locale"], "he")
        self.assertEqual(report["fonts"][0]["uncovered_characters"], [])
        unassigned = report["fonts"][1]
        self.assertIn(
            "ת", [item["character"] for item in unassigned["uncovered_characters"]]
        )
        with tempfile.TemporaryDirectory() as output:
            pack = commands.pack_lang("test", output)
            self.assertEqual(report["pack_size_bytes"], pack.stat().st_size)

    def test_font_limit_and_missing_file_fail(self):
        self.font(pixelHeight=200)
        self.catalog.append(polib.POEntry(msgid="Test", msgstr="א"))
        self.save()
        report = check_lang("test")
        self.assertFalse(report["ok"])
        self.assertIn("universal limit", format_report(report))
        self.font(file="missing.ttf")
        self.save()
        self.assertFalse(check_lang("test")["ok"])

    def test_alias_and_cycle(self):
        self.font()
        self.mapping["fonts"][1] = {"name": FONT_SLOTS[1], "alias": FONT_SLOTS[0]}
        self.save()
        report = check_lang("test")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["fonts"][1]["resolved_slot"], FONT_SLOTS[0])
        self.mapping["fonts"][0]["alias"] = FONT_SLOTS[1]
        self.save()
        self.assertFalse(check_lang("test")["ok"])

    def test_malformed_inputs(self):
        for value in ([], {}, {"strings": {}, "fonts": []}):
            (self.source / commands.LANG_MAP).write_text(json.dumps(value))
            self.assertFalse(check_lang("test")["ok"])
        self.save()
        (self.source / commands.CATALOG).write_text('msgid "unterminated\n')
        self.assertFalse(check_lang("test")["ok"])

    def test_json_cli_stdout_and_exit_status(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for locale, expected in (("fr_FR", 0), ("../invalid", 1), ("not_a_locale", 1)):
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/lang.py"),
                    "check_lang",
                    "--lang",
                    locale,
                    "--json",
                ],
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["ok"], expected == 0)
            self.assertEqual(result.stderr, "")

    def test_base_coverage_and_uploaded_extension(self):
        self.catalog.append(polib.POEntry(msgid="Test", msgstr="éא"))
        self.save()
        report = check_lang("test")
        font = report["fonts"][0]
        self.assertEqual([d["character"] for d in font["uncovered_characters"]], ["א"])
        self.assertNotIn(
            "built_in_coverage_unknown", [i["code"] for i in report["issues"]]
        )
        self.font()
        self.save()
        report = check_lang("test")
        self.assertEqual(report["fonts"][0]["uncovered_characters"], [])
        self.assertEqual(report["fonts"][0]["excluded_characters"], [])
        self.assertEqual(
            [d["character"] for d in report["fonts"][1]["uncovered_characters"]], ["א"]
        )

    def test_alias_keeps_destination_base_coverage(self):
        self.font()
        self.catalog.append(polib.POEntry(msgid="Test", msgstr="Aא"))
        numeric_slot = "BITHAM_42_MEDIUM_NUMBERS_EXTENDED"
        self.mapping["fonts"] = [
            self.mapping["fonts"][0],
            {"name": numeric_slot, "alias": FONT_SLOTS[0]},
        ]
        self.save()
        report = check_lang("test")
        fonts = {font["slot"]: font for font in report["fonts"]}
        self.assertEqual(fonts[FONT_SLOTS[0]]["uncovered_characters"], [])
        self.assertEqual(
            [d["character"] for d in fonts[numeric_slot]["uncovered_characters"]], ["A"]
        )
        self.assertIn(
            numeric_slot,
            next(i for i in report["issues"] if i["code"] == "font_coverage_gap")[
                "slots"
            ],
        )

    def test_invalid_coverage_snapshot_is_an_error(self):
        self.save()
        path = self.root / "coverage.json"
        with patch.object(lang_check, "COVERAGE_PATH", path):
            for data in (None, "{}", '{"schema_version": 99, "fonts": {}}'):
                if data is not None:
                    path.write_text(data)
                report = check_lang("test")
                self.assertFalse(report["ok"])
                self.assertIn(
                    "built_in_coverage_invalid", [i["code"] for i in report["issues"]]
                )

    def test_emoji_is_silently_excluded(self):
        self.catalog.append(polib.POEntry(msgid="Test", msgstr="é❤"))
        self.save()
        report = check_lang("test")
        self.assertEqual(report["fonts"][0]["uncovered_characters"], [])
        self.assertNotIn("unchecked_emoji_characters", report)
        self.assertFalse(any("emoji" in issue["code"] for issue in report["issues"]))
        self.assertNotIn(
            "❤",
            [
                d["character"]
                for font in report["fonts"]
                for d in font["uncovered_characters"]
            ],
        )
