# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import lang_commands as commands
from pack_format import FONT_SLOTS, crc32, serialize

ROOT = Path(__file__).resolve().parents[1]
BODY_START = 12 + 256 * 16


def unpack(data):
    count, checksum, timestamp = struct.unpack_from("<III", data)
    assert count == 21 and timestamp == 0
    assert checksum == crc32(data[BODY_START:])
    contents = []
    for index in range(count):
        file_id, offset, length, checksum = struct.unpack_from(
            "<IIII", data, 12 + index * 16
        )
        assert file_id == index + 1
        content = data[BODY_START + offset : BODY_START + offset + length]
        assert len(content) == length and crc32(content) == checksum
        contents.append(content)
    assert BODY_START + offset + length == len(data)
    return contents


class PackFormatTest(unittest.TestCase):
    def test_crc_matches_firmware_vectors(self):
        self.assertEqual(crc32(b"123456789"), 0xAFF19057)
        self.assertEqual(crc32(b"\xfe\xff\xfe\xff\x88"), 0x495E02CA)
        self.assertEqual(crc32(b""), 0xFFFFFFFF)

    def test_roundtrip_and_last_resource_deduplication(self):
        resources = [b"catalog", b"font", b"other"] + [b""] * 17 + [b"font"]
        data = serialize(resources)
        self.assertEqual(unpack(data), resources)
        self.assertEqual(data[BODY_START:], b"catalogotherfont")
        # Captured from PebbleOS's ResourcePack(False) serializer.
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            "be9baad64350c80c8f8b3c3f99cfe366e93ac4b228eebdbe56e797d0dc976ecb",
        )

    def test_empty_pack(self):
        self.assertEqual(unpack(serialize([b""] * 21)), [b""] * 21)

    def test_invalid_slots_rejected(self):
        with self.assertRaises(ValueError):
            serialize([b""] * 19)
        resource_map = commands.new_map("fr_FR")
        resource_map["fonts"][0]["name"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            commands.validate_map(resource_map)


class LanguageTest(unittest.TestCase):
    def test_catalogs_belong_to_translation_checkout(self):
        self.assertEqual(commands.lang_dir("fr_FR"), ROOT / "fr_FR")

    def test_invalid_locale_cannot_escape_checkout(self):
        for locale in ("../other", "/tmp/other", "", "a/b"):
            with self.subTest(locale=locale), self.assertRaises(ValueError):
                commands.lang_dir(locale)

    def test_new_map_has_all_slots_including_gothic_36(self):
        resource_map = commands.new_map("fr_FR")
        commands.validate_map(resource_map)
        self.assertEqual(tuple(e["name"] for e in resource_map["fonts"]), FONT_SLOTS)

    def test_pack_all_skips_non_pack_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "fr_FR",
                ".git",
                "tools",
                "tests",
                "ja_JP",
                "uk_UA",
                "en_US",
            ):
                (root / name).mkdir()
            for name in ("fr_FR", "uk_UA", "en_US"):
                (root / name / commands.LANG_MAP).write_text("{}")
            (root / "uk_UA" / "INCOMPLETE").write_text("Missing fonts\n")
            with (
                patch.object(commands, "LANG_ROOT", root),
                patch.object(commands, "pack_lang") as pack,
            ):
                commands.pack_all_langs("dist")
            self.assertEqual(
                pack.call_args_list,
                [call("en_US", "dist"), call("fr_FR", "dist"), call("uk_UA", "dist")],
            )

    def test_font_aliases_and_map_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test"
            source.mkdir()
            resource_map = commands.new_map("test")
            resource_map["strings"]["file"] = ""
            resource_map["fonts"][0] = {"name": FONT_SLOTS[0], "alias": FONT_SLOTS[1]}
            resource_map["fonts"][1]["file"] = "font.ttf"
            resource_map["fonts"].reverse()
            (source / commands.LANG_MAP).write_text(json.dumps(resource_map))
            with (
                patch.object(commands, "LANG_ROOT", root),
                patch.object(commands, "build_font", return_value=b"font"),
            ):
                contents = unpack(commands.pack_lang("test", root / "out").read_bytes())
            self.assertEqual(contents[:3], [b"", b"font", b"font"])

    def test_omitted_slots_do_not_shift_resource_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test"
            source.mkdir()
            resource_map = commands.new_map("test")
            resource_map["strings"]["file"] = ""
            resource_map["fonts"] = [{"name": FONT_SLOTS[-1], "file": "font.ttf"}]
            (source / commands.LANG_MAP).write_text(json.dumps(resource_map))
            with (
                patch.object(commands, "LANG_ROOT", root),
                patch.object(commands, "build_font", return_value=b"font"),
            ):
                contents = unpack(commands.pack_lang("test", root / "out").read_bytes())
            self.assertEqual(contents, [b""] * 20 + [b"font"])

    def test_cyclic_alias_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test"
            source.mkdir()
            resource_map = commands.new_map("test")
            resource_map["strings"]["file"] = ""
            resource_map["fonts"][0]["alias"] = FONT_SLOTS[0]
            (source / commands.LANG_MAP).write_text(json.dumps(resource_map))
            with (
                patch.object(commands, "LANG_ROOT", root),
                self.assertRaisesRegex(ValueError, "cyclic"),
            ):
                commands.pack_lang("test", root / "out")

    def test_catalog_init_and_merge_use_explicit_pot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pot = root / "source.pot"
            pot.write_text('msgid "Music"\nmsgstr ""\n')
            with patch.object(commands, "LANG_ROOT", root):
                commands.make_lang("fr_FR", pot)
                catalog = root / "fr_FR/tintin.po"
                self.assertTrue(catalog.exists())
                pot.write_text(
                    'msgid "Music"\nmsgstr ""\n\nmsgid "Settings"\nmsgstr ""\n'
                )
                commands.make_lang("fr_FR", pot)
                self.assertIn('msgid "Settings"', catalog.read_text())
                self.assertEqual(
                    len(
                        json.loads((root / "fr_FR/lang_map.json").read_text())["fonts"]
                    ),
                    20,
                )

    def test_universal_font_limit_has_actionable_error(self):
        entry = json.loads((ROOT / "en_IL/lang_map.json").read_text())["fonts"][0]
        entry["pixelHeight"] = 200
        with self.assertRaisesRegex(ValueError, "universal limit.*Adjust the font"):
            commands.build_font(ROOT / "en_IL", entry, None)

    def test_compressed_font(self):
        entry = json.loads((ROOT / "en_IL/lang_map.json").read_text())["fonts"][0]
        entry["compress"] = "RLE4"
        data = commands.build_font(ROOT / "en_IL", entry, None)
        self.assertEqual(data[0], 3)
        self.assertTrue(data[9] & 2)

    def test_missing_font_has_actionable_error(self):
        entry = {"name": FONT_SLOTS[0], "file": "does-not-exist.ttf"}
        with self.assertRaisesRegex(ValueError, "Cannot load font"):
            commands.build_font(ROOT, entry, None)

    def test_cli_builds_without_firmware_or_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            for locale in ("fr_FR", "en_IL"):
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/lang.py"),
                        "pack_lang",
                        "--lang",
                        locale,
                        "--output",
                        directory,
                    ],
                    cwd=directory,
                    env=env,
                    check=True,
                )
                contents = unpack((Path(directory) / f"{locale}.pbl").read_bytes())
                if locale == "fr_FR":
                    self.assertTrue(contents[0])
                if locale == "en_IL":
                    self.assertTrue(contents[1])
            self.assertEqual(len(list(Path(directory).iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
