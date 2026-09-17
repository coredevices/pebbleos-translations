# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

import gettext
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lang_commands as commands
import polib
import publish_packs
import release_packs as releases
from test_lang import unpack


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
        self.commit()
        self.sequence = 0

    def git(self, *args):
        return releases.git(self.root, *args)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test")

    def build(self, previous=None, rebuild=False):
        self.sequence += 1
        output = self.base / str(self.sequence)
        manifest, changed = releases.build(
            self.root,
            output,
            tag=f"packs-{self.sequence}",
            repository="example/translations",
            previous=previous,
            rebuild=rebuild,
        )
        return output, manifest, changed

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
        first, old, _ = self.build()
        self.template.append(polib.POEntry(msgid="New string"))
        self.template.save(str(self.root / "pebbleos.pot"))
        self.commit()
        with patch.object(commands, "pack_lang", side_effect=AssertionError("Rebuilt")):
            _, new, changed = self.build(first)
        self.assertTrue(changed)
        self.assertEqual(new["languages"][0]["totalStrings"], 2)
        self.assertEqual(new["languages"][0]["translatedStrings"], 1)
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
