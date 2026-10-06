# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import lang_commands as commands
import polib
import release_policy as policy


class PolicyTest(unittest.TestCase):
    def test_custom_font_detection_resolves_aliases(self):
        mapping = commands.new_map("de_DE")
        self.assertFalse(policy.has_custom_fonts(mapping))
        mapping["fonts"][0].update(file="font.ttf")
        mapping["fonts"][1] = {
            "name": mapping["fonts"][1]["name"],
            "alias": mapping["fonts"][0]["name"],
        }
        self.assertTrue(policy.has_custom_fonts(mapping))

    def test_built_in_mode_still_requires_successful_compilation(self):
        self.assertIn(
            "compilation checks failed",
            policy.check_approval(
                {},
                "unused",
                {"ok": False, "fonts": [], "issues": []},
                custom_fonts=False,
            ),
        )

    def test_old_reports_do_not_gate_on_specialized_alphabet_gaps(self):
        report = {
            "fonts": [
                {
                    "slot": slot,
                    "uncovered_characters": [{"codepoint": "U+05D0"}],
                }
                for slot in (
                    "GOTHIC_18_EXTENDED",
                    "BITHAM_42_MEDIUM_NUMBERS_EXTENDED",
                    "BITHAM_18_LIGHT_SUBSET_EXTENDED",
                    "ROBOTO_BOLD_SUBSET_49_EXTENDED",
                )
            ]
        }
        self.assertEqual(policy.gaps(report), {"GOTHIC_18_EXTENDED": [0x05D0]})

    @staticmethod
    def evidence(catalog):
        return {
            "schemaVersion": 2,
            "kind": "translation",
            "publicationEnabled": True,
            "minimumApprovedPercent": 80,
            "component": "pebbleos/watch",
            "language": "fr",
            "locale": "fr_FR",
            "reviewEnabled": True,
            "approvedOnlyCommits": True,
            "reviewers": ["reviewer"],
            "catalog": str(catalog),
        }

    def test_api_export_is_approved_only_and_authenticated(self):
        catalog = polib.POFile()
        catalog.metadata = {"Language": "fr"}
        catalog.append(polib.POEntry(msgid="Hello", msgstr="Bonjour"))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            self.evidence(catalog)
        ).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with (
            patch.dict(os.environ, {"WEBLATE_API_TOKEN": "test-token"}),
            patch.object(policy, "build_opener", return_value=opener),
        ):
            exported = polib.pofile(policy.release_evidence("fr_FR")["catalog"])
        self.assertEqual(exported[0].msgstr, "Bonjour")
        request = opener.open.call_args.args[0]
        self.assertIn("/api/pebble/release/fr_FR/", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Token test-token")
        self.assertEqual(
            request.get_header("User-agent"), "pebble-language-pack-publisher/0.1"
        )
        self.assertTrue(request.full_url.startswith(policy.WEBLATE_ORIGIN + "/"))
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "required"),
        ):
            policy.release_evidence("fr_FR")
        with self.assertRaises(ValueError):
            policy.NoRedirects().redirect_request(
                None, None, 302, "", {}, "https://untrusted.invalid"
            )

    def test_export_language_and_size_are_checked(self):
        response = MagicMock()
        opener = MagicMock()
        opener.open.return_value = response
        catalog = polib.POFile()
        catalog.metadata = {"Language": "de"}
        with (
            patch.dict(os.environ, {"WEBLATE_API_TOKEN": "test"}),
            patch.object(policy, "build_opener", return_value=opener),
        ):
            response.__enter__.return_value.read.return_value = json.dumps(
                self.evidence(catalog)
            ).encode()
            with self.assertRaisesRegex(ValueError, "different language"):
                policy.release_evidence("fr_FR")
            response.__enter__.return_value.read.return_value = b"x" * (
                10 * 1024 * 1024 + 1
            )
            with self.assertRaisesRegex(ValueError, "10 MiB"):
                policy.release_evidence("fr_FR")

    def test_disabled_reviews_wrong_locale_and_revoked_reviewer_are_rejected(self):
        catalog = polib.POFile()
        catalog.metadata = {"Language": "fr"}
        response = MagicMock()
        opener = MagicMock()
        opener.open.return_value = response
        for field, value in (
            ("component", "other/watch"),
            ("reviewEnabled", False),
            ("approvedOnlyCommits", False),
            ("locale", "de_DE"),
            ("reviewers", []),
        ):
            evidence = self.evidence(catalog)
            evidence[field] = value
            response.__enter__.return_value.read.return_value = json.dumps(
                evidence
            ).encode()
            with (
                self.subTest(field=field),
                patch.dict(os.environ, {"WEBLATE_API_TOKEN": "test"}),
                patch.object(policy, "build_opener", return_value=opener),
                self.assertRaises(ValueError),
            ):
                policy.release_evidence("fr_FR")

    def test_font_license_subsetting_and_alias_bytes_invalidate_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "font.ttf").write_bytes(b"font bytes")
            (source / "LICENSE").write_text("redistribution license")
            (source / "characters.json").write_text("[65]")
            mapping = commands.new_map("fr_FR")
            mapping["fonts"][0].update(file="font.ttf", characterList="characters.json")
            mapping["fonts"][1] = {
                "name": mapping["fonts"][1]["name"],
                "alias": mapping["fonts"][0]["name"],
            }
            original = policy.font_inputs(source, mapping)
            for name in ("font.ttf", "LICENSE", "characters.json"):
                saved = (source / name).read_bytes()
                (source / name).write_bytes(saved + b"changed")
                self.assertNotEqual(original, policy.font_inputs(source, mapping))
                (source / name).write_bytes(saved)
            changed = copy.deepcopy(mapping)
            changed["fonts"][0]["pixelHeight"] = 13
            self.assertNotEqual(original, policy.font_inputs(source, changed))
            changed["fonts"][0]["file"] = "../outside.ttf"
            with self.assertRaisesRegex(ValueError, "language folder"):
                policy.font_inputs(source, changed)
            (source / "LICENSE").unlink()
            with self.assertRaisesRegex(ValueError, "license"):
                policy.font_inputs(source, mapping)

    def test_web_settings_cannot_lower_80_percent_bar(self):
        catalog = polib.POFile()
        catalog.metadata = {"Language": "fr"}
        data = self.evidence(catalog)
        data["minimumApprovedPercent"] = 79
        with self.assertRaisesRegex(ValueError, "80"):
            policy.validate_evidence(data, "fr_FR")

    def test_missing_and_stale_web_font_approval_are_rejected(self):
        report = {"ok": True, "fonts": [], "issues": []}
        self.assertIn(
            "out of date",
            policy.check_approval({"fontApproval": None}, "current", report),
        )
        data = {
            "fontApproval": {
                "approvedBy": "maintainer",
                "inputHash": "old",
                "redistributionConfirmed": True,
                "renderingReviewed": True,
                "note": "review",
            }
        }
        self.assertIn("out of date", policy.check_approval(data, "current", report))
