# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import copy
import gettext
import io
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lang_commands as commands
import polib
import publish_packs
import release_packs as releases
import release_policy as policy
from test_lang import unpack


def firmware_lookup(mo, key):
    """Exercise the hash-table lookup contract from PebbleOS services/i18n/i18n.c."""
    magic, _, count, originals, translations, hash_size, hash_offset = (
        struct.unpack_from("<7I", mo)
    )
    assert magic == 0x950412DE
    assert hash_size > 2, "Firmware rejects catalogs without a lookup hash table"
    key = key.encode("utf-8")
    value = 0
    for byte in key:
        value = ((value << 4) + byte) & 0xFFFFFFFF
        high = value & 0xF0000000
        value ^= high ^ (high >> 24)
    index = value % hash_size
    step = value % (hash_size - 2) + 1
    for _ in range(hash_size):
        number = struct.unpack_from("<I", mo, hash_offset + 4 * index)[0]
        if number == 0:
            return None
        assert number <= count
        size, offset = struct.unpack_from("<II", mo, originals + 8 * (number - 1))
        if mo[offset : offset + size] == key:
            size, offset = struct.unpack_from(
                "<II", mo, translations + 8 * (number - 1)
            )
            return mo[offset : offset + size]
        index = (index + step) % hash_size
    raise AssertionError("Catalog hash lookup never terminates")


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.locale = self.root / "fr_FR"
        self.locale.mkdir()
        (self.locale / "lang_map.json").write_text(
            json.dumps(commands.new_map("fr_FR"))
        )
        self.catalog = polib.POFile()
        self.catalog.metadata = {
            "Project-Id-Version": "38.0",
            "Language": "fr_FR",
            "Language-Team": "French",
            "Name": "Français",
            "Content-Type": "text/plain; charset=UTF-8",
            "Plural-Forms": "nplurals=2; plural=(n > 1);",
        }
        self.catalog.append(polib.POEntry(msgid="Hello", msgstr="Bonjour"))
        self.catalog.save(str(self.locale / "tintin.po"))
        self.template = polib.POFile()
        self.template.append(polib.POEntry(msgid="Hello"))
        self.template.save(str(self.root / "pebbleos.pot"))
        self.evidence = {
            "schemaVersion": 2,
            "component": "pebbleos/watch",
            "locale": "fr_FR",
            "publicationEnabled": True,
            "reviewEnabled": True,
            "approvedOnlyCommits": True,
            "minimumApprovedPercent": 80,
            "kind": "translation",
            "language": "fr",
            "reviewers": ["native-speaker"],
            "fontApproval": {
                "approvedBy": "maintainer",
                "inputHash": policy.font_inputs(self.locale, commands.new_map("fr_FR")),
                "redistributionConfirmed": True,
                "renderingReviewed": True,
                "note": "Reviewed built-in fonts; no third-party uploads",
                "acceptedMissingCharacters": {},
            },
        }
        with patch.object(commands, "LANG_ROOT", self.root):
            accepted = policy.gaps(releases.check_lang("fr_FR"))
        # Fixture approves intentional ASCII gaps in numeric/subset styles.
        # Real policies accept only the points a maintainer explicitly reviews.
        for slot in commands.FONT_SLOTS:
            if not slot.startswith("GOTHIC_"):
                accepted[slot] = sorted(
                    set(accepted.get(slot, [])) | set(range(32, 127))
                )
        self.evidence["fontApproval"]["acceptedMissingCharacters"] = accepted
        self.commit()
        self.sequence = 0

    def git(self, *args):
        return releases.git(self.root, *args)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test")

    def build(self, previous=None, rebuild=False, approved=None):
        self.sequence += 1
        output = self.base / str(self.sequence)
        manifest, changed = releases.build(
            self.root,
            output,
            tag=f"packs-{self.sequence}",
            repository="example/translations",
            previous=previous,
            rebuild=rebuild,
            evidence_provider=lambda locale: self.web_evidence(locale, approved),
        )
        return output, manifest, changed

    def web_evidence(self, locale, approved=None):
        evidence = copy.deepcopy(self.evidence)
        evidence["locale"] = locale
        catalog = copy.deepcopy(self.catalog if approved is None else approved)
        catalog.metadata["Language"] = evidence["language"]
        evidence["catalog"] = str(catalog)
        return evidence

    def test_stamped_catalog_is_readable_by_firmware(self):
        self.catalog.append(
            polib.POEntry(msgid="Open", msgctxt="menu", msgstr="Ouvrir")
        )
        self.template.append(polib.POEntry(msgid="Open", msgctxt="menu"))
        for index in range(40):
            self.catalog.append(
                polib.POEntry(msgid=f"String {index}", msgstr=f"Texte {index}")
            )
            self.template.append(polib.POEntry(msgid=f"String {index}"))
        self.template.save(str(self.root / "pebbleos.pot"))
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        directory, _, _ = self.build()
        original = (directory / "fr_FR.pbl").read_bytes()
        for version in (1, 2, 65535):
            stamped = releases.stamp_version(original, version)
            resources = unpack(stamped)
            self.assertEqual(resources[1:], unpack(original)[1:])
            header = firmware_lookup(resources[0], "")[:399]
            self.assertIn(f"Project-Id-Version: {version}\n".encode(), header)
            self.assertIn(b"Language: fr_FR\n", header)
            self.assertIn("Name: Français\n".encode(), header)
            self.assertEqual(firmware_lookup(resources[0], "Hello"), b"Bonjour")
            self.assertEqual(firmware_lookup(resources[0], "menu\x04Open"), b"Ouvrir")
            for index in range(40):
                self.assertEqual(
                    firmware_lookup(resources[0], f"String {index}"),
                    f"Texte {index}".encode(),
                )
            self.assertIsNone(firmware_lookup(resources[0], "Missing string"))

    def test_missing_watch_display_name_uses_catalog_name(self):
        del self.catalog.metadata["Name"]
        self.catalog.metadata["Language-Team"] = "French <team@example.invalid>"
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        directory, _, _ = self.build()
        mo = unpack((directory / "fr_FR.pbl").read_bytes())[0]
        self.assertIn(b"Name: French\n", firmware_lookup(mo, "")[:399])

    def test_versions_reuse_rebuild_and_source_unchanged(self):
        original = (self.locale / "tintin.po").read_bytes()
        first, manifest, changed = self.build()
        self.assertTrue(changed)
        entry = manifest["languages"][0]
        self.assertEqual(entry["version"], 1)
        strings = gettext.GNUTranslations(
            io.BytesIO(unpack((first / "fr_FR.pbl").read_bytes())[0])
        )
        self.assertEqual(strings.info()["project-id-version"], "1")
        self.assertEqual(strings.gettext("Hello"), "Bonjour")
        self.assertEqual((self.locale / "tintin.po").read_bytes(), original)
        with patch.object(
            commands, "pack_lang", side_effect=AssertionError("Unchanged pack rebuilt")
        ):
            second, same, changed = self.build(first)
        self.assertFalse(changed)
        self.assertEqual(same["languages"][0]["updatedAt"], entry["updatedAt"])
        self.assertIn("/packs-2/", same["languages"][0]["url"])
        rebuilt, same, changed = self.build(second, rebuild=True)
        self.assertFalse(changed)
        self.assertEqual(
            (first / "fr_FR.pbl").read_bytes(), (rebuilt / "fr_FR.pbl").read_bytes()
        )
        self.catalog[0].msgstr = "Salut"
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        _, updated, changed = self.build(rebuilt)
        self.assertTrue(changed)
        self.assertEqual(updated["languages"][0]["version"], 2)

    def test_documentation_skips_release_and_shared_changes_rebuild(self):
        first, _, _ = self.build()
        (self.root / "README.md").write_text("Documentation change")
        self.commit()
        with patch.object(commands, "pack_lang", side_effect=AssertionError("Rebuilt")):
            second, _, changed = self.build(first)
        self.assertFalse(changed)
        (self.root / "tools").mkdir()
        (self.root / "tools" / "builder.py").write_text("# Shared change")
        self.commit()
        with patch.object(commands, "pack_lang", wraps=commands.pack_lang) as pack:
            _, manifest, changed = self.build(second)
        pack.assert_called_once()
        self.assertTrue(changed)
        self.assertEqual(manifest["languages"][0]["version"], 1)

    def test_template_only_change_updates_completion_not_pack(self):
        for index in range(3):
            self.catalog.append(
                polib.POEntry(msgid=f"String {index}", msgstr=f"Texte {index}")
            )
            self.template.append(polib.POEntry(msgid=f"String {index}"))
        self.catalog.save(str(self.locale / "tintin.po"))
        self.template.save(str(self.root / "pebbleos.pot"))
        self.commit()
        first, old, _ = self.build()
        self.template.append(polib.POEntry(msgid="New string"))
        self.template.save(str(self.root / "pebbleos.pot"))
        self.commit()
        with patch.object(commands, "pack_lang", side_effect=AssertionError("Rebuilt")):
            _, new, changed = self.build(first)
        self.assertTrue(changed)
        self.assertEqual(new["languages"][0]["totalStrings"], 5)
        self.assertEqual(new["languages"][0]["translatedStrings"], 4)
        for key in ("version", "updatedAt", "sha256"):
            self.assertEqual(new["languages"][0][key], old["languages"][0][key])

    def test_removed_and_reintroduced_locale_does_not_reuse_version(self):
        first, _, _ = self.build()
        saved = self.base / "saved"
        shutil.copytree(self.locale, saved)
        shutil.rmtree(self.locale)
        self.commit()
        second, deleted, changed = self.build(first)
        self.assertTrue(changed)
        self.assertEqual(deleted["languages"], [])
        self.assertEqual(deleted["versionHighWater"]["fr_FR"], 1)
        shutil.copytree(saved, self.locale)
        self.commit()
        _, restored, _ = self.build(second)
        self.assertEqual(restored["languages"][0]["version"], 2)

    def test_corrupt_previous_asset_fails_closed(self):
        first, _, _ = self.build()
        (first / "fr_FR.pbl").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum/size"):
            self.build(first)

    def test_version_exhaustion_fails(self):
        first, manifest, _ = self.build()
        manifest["versionHighWater"]["fr_FR"] = 65535
        (first / "manifest.json").write_text(json.dumps(manifest))
        self.catalog[0].msgstr = "Salut"
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        with self.assertRaisesRegex(ValueError, "16-bit"):
            self.build(first)

    def test_missing_map_fails_instead_of_omitting_language(self):
        (self.locale / "lang_map.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()

    def test_community_language_is_not_published(self):
        self.evidence["reviewers"] = []
        directory, manifest, changed = self.build()
        self.assertFalse(changed)
        self.assertEqual(manifest["languages"], [])
        self.assertFalse((directory / "fr_FR.pbl").exists())
        self.assertIn("Community language", (directory / "readiness.json").read_text())

    def test_missing_reviewer_holds_last_release_without_rebuilding(self):
        first, old, _ = self.build()
        self.evidence["reviewers"] = []
        self.catalog[0].msgstr = "Unreviewed update"
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        with patch.object(
            commands, "pack_lang", side_effect=AssertionError("Held pack rebuilt")
        ):
            second, manifest, changed = self.build(first)
        self.assertFalse(changed)
        self.assertEqual(
            manifest["languages"][0]["version"], old["languages"][0]["version"]
        )
        self.assertEqual(
            (first / "fr_FR.pbl").read_bytes(), (second / "fr_FR.pbl").read_bytes()
        )

    def test_exact_80_percent_and_only_approved_targets(self):
        for index in range(4):
            self.template.append(polib.POEntry(msgid=f"String {index}"))
            self.catalog.append(
                polib.POEntry(msgid=f"String {index}", msgstr=f"Texte {index}")
            )
        self.template.save(str(self.root / "pebbleos.pot"))
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        approved = copy.deepcopy(self.catalog)
        approved.pop()
        directory, manifest, changed = self.build(approved=approved)
        self.assertTrue(changed)
        self.assertEqual(manifest["languages"][0]["translatedStrings"], 4)
        mo = gettext.GNUTranslations(
            io.BytesIO(unpack((directory / "fr_FR.pbl").read_bytes())[0])
        )
        self.assertEqual(mo.gettext("String 2"), "Texte 2")
        self.assertEqual(mo.gettext("String 3"), "String 3")
        approved.pop()
        held, _, changed = self.build(directory, approved=approved)
        self.assertFalse(changed)
        self.assertEqual(
            (held / "fr_FR.pbl").read_bytes(), (directory / "fr_FR.pbl").read_bytes()
        )
        self.assertIn("80%", (held / "readiness.json").read_text())

    def test_font_revision_and_revoked_maintainer_approval_are_held(self):
        first, _, _ = self.build()
        for change in ("revoked", "rendering", "font"):
            with self.subTest(change=change):
                approval = self.evidence["fontApproval"]
                old = dict(approval)
                if change == "revoked":
                    approval["approvedBy"] = None
                elif change == "rendering":
                    approval["renderingReviewed"] = False
                else:
                    mapping = commands.new_map("fr_FR")
                    mapping["fonts"][0].update(
                        file="changed.ttf", license="license.txt"
                    )
                    (self.locale / "changed.ttf").write_bytes(b"changed font")
                    (self.locale / "license.txt").write_text("license")
                    (self.locale / "lang_map.json").write_text(json.dumps(mapping))
                directory, _, changed = self.build(first)
                self.assertFalse(changed)
                self.assertIn("out of date", (directory / "readiness.json").read_text())
                self.evidence["fontApproval"] = old

    def test_new_missing_glyphs_hold_until_explicitly_accepted(self):
        first, _, _ = self.build()
        self.catalog[0].msgstr = "漢字"
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        held, _, changed = self.build(first)
        self.assertFalse(changed)
        readiness = json.loads((held / "readiness.json").read_text())["languages"][0]
        self.assertIn("coverage gaps", readiness["reasons"][0])
        self.evidence["fontApproval"]["acceptedMissingCharacters"] = policy.gaps(
            readiness["checks"]
        )
        _, manifest, changed = self.build(first)
        self.assertTrue(changed)
        self.assertEqual(manifest["languages"][0]["version"], 2)

    def test_export_failure_or_unsynchronized_approval_never_publishes(self):
        first, _, _ = self.build()
        export = copy.deepcopy(self.catalog)
        export[0].msgstr = "Not committed"
        held, _, changed = self.build(first, approved=export)
        self.assertFalse(changed)
        self.assertIn("synchronized", (held / "readiness.json").read_text())
        with patch.object(
            policy, "release_evidence", side_effect=OSError("Weblate offline")
        ):
            directory = self.base / "offline"
            _, changed = releases.build(
                self.root,
                directory,
                tag="packs-offline",
                repository="example/translations",
                previous=first,
            )
        self.assertFalse(changed)
        self.assertIn("Weblate offline", (directory / "readiness.json").read_text())

    def test_source_format_flags_gate_bad_placeholders(self):
        self.template[0].msgid = "Hello %s"
        self.template[0].flags = ["c-format"]
        self.catalog[0].msgid = "Hello %s"
        self.catalog[0].msgstr = "Bonjour"
        self.template.save(str(self.root / "pebbleos.pot"))
        self.catalog.save(str(self.locale / "tintin.po"))
        self.commit()
        directory, manifest, changed = self.build()
        self.assertFalse(changed)
        self.assertEqual(manifest["languages"], [])
        self.assertIn(
            "compilation checks failed", (directory / "readiness.json").read_text()
        )

    def test_font_only_pack_needs_font_review_but_exports_no_custom_strings(self):
        self.locale.rename(self.root / "en_IL")
        self.locale = self.root / "en_IL"
        self.evidence.update(kind="font-only", coverageLanguage="en", reviewers=[])
        self.commit()
        manifest, changed = releases.build(
            self.root,
            self.base / "font-only",
            tag="packs-font-only",
            repository="example/translations",
            evidence_provider=self.web_evidence,
        )
        self.assertTrue(changed)
        self.assertEqual(manifest["languages"][0]["translatedStrings"], 0)
        pack = self.base / "font-only" / "en_IL.pbl"
        strings = gettext.GNUTranslations(io.BytesIO(unpack(pack.read_bytes())[0]))
        self.assertEqual(strings.gettext("Hello"), "Hello")

    def test_completion_excludes_formatting_sources_but_keeps_missing_text(self):
        template = polib.POFile()
        catalog = polib.POFile()
        for context, source, translation in (
            ("suffix", "", ""),
            ("separator", " ", " "),
            ("layout", "\t\n", "formatting"),
            (None, "Hello", "Bonjour"),
            (None, "Missing", ""),
        ):
            template.append(polib.POEntry(msgctxt=context, msgid=source))
            catalog.append(
                polib.POEntry(msgctxt=context, msgid=source, msgstr=translation)
            )
        template.append(polib.POEntry(msgid="Not in catalog"))
        # A nonblank plural still requires translation even if its singular is blank.
        template.append(polib.POEntry(msgctxt="count", msgid="", msgid_plural="items"))
        self.assertEqual(
            releases.completion(catalog, template),
            {"translatedStrings": 1, "totalStrings": 4},
        )
        self.assertEqual(len(template), 7)
        formatting_only = polib.POFile()
        formatting_only.extend(template[:3])
        self.assertEqual(
            releases.completion(catalog, formatting_only),
            {"translatedStrings": 0, "totalStrings": 0},
        )

    def test_completion_excludes_fuzzy_obsolete_missing_plural_and_old_sources(self):
        template = polib.POFile()
        catalog = polib.POFile()
        catalog.metadata["Plural-Forms"] = "nplurals=2; plural=n != 1;"
        for entry in [
            polib.POEntry(msgid="one", msgid_plural="many"),
            polib.POEntry(msgid="Hello", msgctxt="menu"),
            polib.POEntry(msgid="fuzzy"),
            polib.POEntry(msgid="gone"),
        ]:
            template.append(entry)
        catalog.extend(
            [
                polib.POEntry(
                    msgid="one", msgid_plural="many", msgstr_plural={0: "un"}
                ),
                polib.POEntry(msgid="Hello", msgctxt="menu", msgstr="Bonjour"),
                polib.POEntry(msgid="fuzzy", msgstr="Oui", flags=["fuzzy"]),
                polib.POEntry(msgid="gone", msgstr="Parti", obsolete=True),
                polib.POEntry(msgid="old source", msgstr="Ancien"),
            ]
        )
        self.assertEqual(
            releases.completion(catalog, template),
            {"translatedStrings": 1, "totalStrings": 4},
        )


class PublicationTest(unittest.TestCase):
    def test_rollback_does_not_select_older_latest_as_version_baseline(self):
        releases_list = [
            [
                {"id": 2, "tag_name": "packs-2", "draft": False, "prerelease": False},
                {"id": 3, "tag_name": "packs-3", "draft": True, "prerelease": False},
                {"id": 1, "tag_name": "packs-1", "draft": False, "prerelease": False},
            ]
        ]
        with patch.object(publish_packs, "gh", return_value=json.dumps(releases_list)):
            self.assertEqual(publish_packs.newest_snapshot("example/repo")["id"], 2)

    def test_stale_run_does_not_create_release(self):
        with (
            patch.object(publish_packs, "current_main", return_value=False),
            patch.object(publish_packs, "gh") as gh,
        ):
            publish_packs.publish("example/repo", Path("unused"), "packs-1", "old")
            gh.assert_not_called()

    def test_publish_only_after_download_verification_and_second_stale_check(self):
        for corrupted, advanced in ((False, False), (True, False), (False, True)):
            with (
                self.subTest(corrupted=corrupted, advanced=advanced),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory)
                (output / "manifest.json").write_text("test manifest")
                (output / "fr_FR.pbl").write_bytes(b"test pack")

                def gh(*args, output=output, corrupted=corrupted):
                    if args[:2] == ("release", "download"):
                        destination = Path(args[args.index("--dir") + 1])
                        for asset in output.iterdir():
                            shutil.copyfile(asset, destination / asset.name)
                        if corrupted:
                            (destination / "fr_FR.pbl").write_bytes(b"corrupt")
                    return ""

                with (
                    patch.object(
                        publish_packs, "current_main", side_effect=[True, not advanced]
                    ),
                    patch.object(publish_packs, "gh", side_effect=gh) as command,
                ):
                    if corrupted:
                        with self.assertRaisesRegex(ValueError, "verification"):
                            publish_packs.publish(
                                "example/repo", output, "packs-1", "sha"
                            )
                    else:
                        publish_packs.publish("example/repo", output, "packs-1", "sha")
                    published = any(
                        c.args[:2] == ("release", "edit")
                        for c in command.call_args_list
                    )
                    self.assertEqual(published, not corrupted and not advanced)
                    if published:
                        self.assertEqual(
                            command.call_args.args[-2:], ("--draft=false", "--latest")
                        )

    def test_upload_failure_leaves_draft(self):
        with tempfile.TemporaryDirectory() as directory:

            def gh(*args):
                if args[:2] == ("release", "upload"):
                    raise subprocess.CalledProcessError(1, "upload")
                return ""

            with (
                patch.object(publish_packs, "current_main", return_value=True),
                patch.object(publish_packs, "gh", side_effect=gh) as command,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    publish_packs.publish(
                        "example/repo", Path(directory), "packs-1", "sha"
                    )
                self.assertFalse(
                    any(
                        c.args[:2] == ("release", "edit")
                        for c in command.call_args_list
                    )
                )


if __name__ == "__main__":
    unittest.main()
