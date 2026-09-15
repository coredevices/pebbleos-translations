# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

import polib
from generate_codepoint_requirements import generate_codepoint_requirements
from language_characters import language_characters


class LanguageCharactersTest(unittest.TestCase):
    def requirements(self, language, text=""):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.po"
            po = polib.POFile()
            po.metadata["Language"] = "fr"
            po.append(polib.POEntry(msgid="text", msgstr=text))
            po.save(str(path))
            return generate_codepoint_requirements(path, language=language)

    def test_selected_language_overrides_header_and_covers_untranslated_letters(self):
        result = self.requirements("he", "ש")
        self.assertEqual(result["baseline_locale"], "he")
        self.assertTrue(
            set(map(ord, "אבגדהוזחטיכךלמםנןסעפףצץקרשת")) <= set(result["codepoints"])
        )

    def test_regions_scripts_and_english_with_extra_glyphs(self):
        self.assertEqual(language_characters("he_IL")[1], language_characters("he")[1])
        self.assertEqual(language_characters("en_IL")[1], language_characters("en")[1])
        self.assertIn(ord("č"), language_characters("sr_Latn_RS")[1])
        self.assertNotIn(ord("ч"), language_characters("sr_Latn_RS")[1])
        self.assertIn(ord("Ж"), language_characters("ru")[1])

    def test_extra_text_shaping_and_emoji(self):
        result = set(self.requirements("ar", "漢🙂")["codepoints"])
        self.assertIn(ord("漢"), result)
        self.assertIn(0xFE91, result)  # Arabic beh initial form.
        self.assertNotIn(ord("🙂"), result)

    def test_unknown_language_reports_no_baseline(self):
        result = self.requirements("xx-Unknown", "Ä")
        self.assertIsNone(result["baseline_locale"])
        self.assertIn(ord("Ä"), result["codepoints"])
