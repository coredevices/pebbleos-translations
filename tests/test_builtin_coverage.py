# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import json
import struct
import unittest
from pathlib import Path

from extract_builtin_coverage import pbf_codepoints
from pack_format import FONT_SLOTS


class BuiltinCoverageTest(unittest.TestCase):
    def test_pbf_versions_offsets_and_wildcard(self):
        for version, wide, small_offsets in (
            (2, False, False),
            (3, False, True),
            (3, True, False),
        ):
            with self.subTest(version=version, wide=wide, small_offsets=small_offsets):
                mappings = [(0x41, 9), (0x42, 0), (0x25AF, 4), (0x43, 4)]
                if wide:
                    mappings.append((0x1F600, 14))
                entry_format = (
                    "<" + ("I" if wide else "H") + ("H" if small_offsets else "I")
                )
                entries = b"".join(
                    struct.pack(entry_format, *entry) for entry in mappings
                )
                header = struct.pack(
                    "<BBHHBB", version, 14, len(mappings), 0x25AF, 1, 4 if wide else 2
                )
                if version == 3:
                    header += bytes([10, int(small_offsets)])
                data = (
                    header
                    + struct.pack("<BBH", 0, len(mappings), 0)
                    + entries
                    + bytes(19)
                )
                self.assertEqual(
                    pbf_codepoints(data), [0x41, 0x1F600] if wide else [0x41]
                )
                with self.assertRaises((ValueError, struct.error)):
                    pbf_codepoints(data[:10])

    def test_snapshot_is_complete_and_preserves_slot_differences(self):
        path = Path(__file__).resolve().parents[1] / "data/builtin_font_coverage.json"
        snapshot = json.loads(path.read_text())
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(set(snapshot["fonts"]), set(FONT_SLOTS))
        self.assertRegex(snapshot["source"]["revision"], r"^[0-9a-f]{40}$")
        for font in snapshot["fonts"].values():
            cps = font["codepoints"]
            self.assertEqual(cps, sorted(set(cps)))
            self.assertNotIn(0x25AF, cps)
            self.assertIn(font["file"], snapshot["source"]["input_sha256"])
        gothic = snapshot["fonts"]["GOTHIC_14_EXTENDED"]["codepoints"]
        numbers = snapshot["fonts"]["BITHAM_42_MEDIUM_NUMBERS_EXTENDED"]["codepoints"]
        self.assertIn(ord("é"), gothic)
        self.assertNotIn(ord("א"), gothic)
        self.assertIn(ord("0"), numbers)
        self.assertNotIn(ord("A"), numbers)
        self.assertIn(
            ord("é"), snapshot["fonts"]["ROBOTO_CONDENSED_21_EXTENDED"]["codepoints"]
        )
