# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Build a complete release directory without accessing GitHub or changing sources."""

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import polib

if __package__:
    from . import lang_commands as commands
    from . import release_policy as policy_tools
    from .lang_check import check_lang
    from .pack_format import FONT_SLOTS, TABLE_SIZE, serialize
else:
    import lang_commands as commands
    import release_policy as policy_tools
    from lang_check import check_lang
    from pack_format import FONT_SLOTS, TABLE_SIZE, serialize

SHARED = (
    "tools",
    "data",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    ".github/workflows/packs.yml",
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def input_state(root, locale):
    paths = (*SHARED, locale)
    # Tree entries include names, modes, and blob IDs; no wall-clock timestamps.
    tree = git(root, "ls-tree", "-r", "HEAD", "--", *paths)
    changed_at = git(root, "log", "-1", "--format=%cI", "--", *paths)
    return digest(tree.encode()), changed_at


def stamp_version(data, version):
    """Replace only the gettext header; reuse the expensive compiled font resources."""
    if not 1 <= version <= 65535:
        raise ValueError("Pack version exceeds the firmware's 16-bit range")
    body = data[12 + TABLE_SIZE * 16 :]
    resources = []
    for index in range(1 + len(FONT_SLOTS)):
        _, offset, size, _ = struct.unpack_from("<IIII", data, 12 + index * 16)
        resources.append(body[offset : offset + size])
    if not resources[0]:
        raise ValueError("Published packs require a translation catalog")
    catalog = polib.mofile(resources[0])
    catalog.metadata["Project-Id-Version"] = str(version)
    if not catalog.metadata.get("Name"):
        catalog.metadata["Name"] = re.sub(
            r"\s*<[^>]*>\s*$", "", catalog.metadata.get("Language-Team", "")
        ).strip() or catalog.metadata.get("Language", "Unknown")
    # polib's MO writer omits the hash table required by the firmware loader.
    with tempfile.TemporaryDirectory(prefix="stamp-language-") as directory:
        po = Path(directory) / "strings.po"
        mo = Path(directory) / "strings.mo"
        catalog.save_as_pofile(str(po))
        commands.compile_catalog(po, mo)
        resources[0] = mo.read_bytes()
    return serialize(resources)


def completion(catalog, template):
    entries = {(e.msgctxt, e.msgid): e for e in catalog if not e.obsolete}
    plural = re.search(
        r"nplurals\s*=\s*(\d+)", catalog.metadata.get("Plural-Forms", "")
    )
    nplurals = int(plural[1]) if plural else 0
    translated = 0
    # Empty suffixes and whitespace separators are formatting, not translation work.
    source = [
        e
        for e in template
        if not e.obsolete and (e.msgid.strip() or e.msgid_plural.strip())
    ]
    for entry in source:
        target = entries.get((entry.msgctxt, entry.msgid))
        if not target or target.fuzzy or target.msgid_plural != entry.msgid_plural:
            continue
        if entry.msgid_plural:
            translated += bool(nplurals) and all(
                target.msgstr_plural.get(i, "").strip() for i in range(nplurals)
            )
        else:
            translated += bool(target.msgstr.strip())
    return {"translatedStrings": translated, "totalStrings": len(source)}


def read_previous(directory):
    if directory is None:
        return None
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["schemaVersion"] != 1:
        raise ValueError("Unsupported previous manifest schema")
    return manifest


def build(
    root,
    output,
    *,
    tag,
    repository,
    previous=None,
    rebuild=False,
    evidence_provider=None,
):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not re.fullmatch(r"packs-[A-Za-z0-9._-]+", tag):
        raise ValueError(
            "Release tag must start with packs- and contain safe URL characters"
        )
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Expected owner/repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    previous = Path(previous).resolve() if previous else None
    old = read_previous(previous)
    old_languages = (
        {entry["locale"]: entry for entry in old["languages"]} if old else {}
    )
    versions = dict(old.get("versionHighWater", {})) if old else {}
    template = polib.pofile(str(root / "pebbleos.pot"))
    evidence_provider = evidence_provider or policy_tools.release_evidence
    languages = []
    readiness = []
    previous_root = commands.LANG_ROOT
    commands.LANG_ROOT = root
    try:
        # Discover catalogs as well as maps so a missing map cannot silently drop a language.
        locales = sorted(
            p
            for p in root.iterdir()
            if p.is_dir()
            and ((p / commands.CATALOG).is_file() or (p / commands.LANG_MAP).is_file())
        )
        for source in locales:
            locale = source.name
            commands.lang_dir(locale)  # Validate names before creating asset paths.
            prior = old_languages.get(locale)
            path = output / f"{locale}.pbl"
            if prior:
                original = (previous / path.name).read_bytes()
                if (
                    len(original) != prior["size"]
                    or digest(original) != prior["sha256"]
                ):
                    raise ValueError(
                        f"Previous {locale} pack failed checksum/size verification"
                    )
            status = {"locale": locale, "status": "held", "reasons": []}
            readiness.append(status)
            record = {}
            font_only = False
            resource_map = json.loads((source / commands.LANG_MAP).read_text())
            catalog_path = policy_tools.asset(source, resource_map["strings"]["file"])
            original_catalog = polib.pofile(str(catalog_path))
            fingerprint, updated_at = input_state(root, locale)
            with tempfile.TemporaryDirectory(prefix="approved-release-") as directory:
                snapshot = Path(directory)
                try:
                    record = evidence_provider(locale)
                    policy_tools.validate_evidence(record, locale)
                    font_only = record["kind"] == "font-only"
                    font_hash = policy_tools.font_inputs(source, resource_map)
                    custom_fonts = policy_tools.has_custom_fonts(resource_map)
                    status["customFonts"] = custom_fonts
                    reason = policy_tools.check_approval(
                        record,
                        font_hash,
                        {"ok": True, "fonts": [], "issues": []},
                        custom_fonts=custom_fonts,
                    )
                    if reason:
                        status["reasons"].append(reason)
                    if not status["reasons"]:
                        if font_only:
                            coverage_language = record["coverageLanguage"]
                            catalog = polib.POFile()
                            catalog.metadata = dict(original_catalog.metadata)
                        else:
                            approved = polib.pofile(
                                record["catalog"], check_for_duplicates=True
                            )
                            catalog = policy_tools.release_catalog(
                                original_catalog, template, approved
                            )
                            progress = completion(catalog, template)
                            status.update(progress)
                            total = progress["totalStrings"]
                            if (
                                not total
                                or progress["translatedStrings"] * 100
                                < total * record["minimumApprovedPercent"]
                            ):
                                raise ValueError(
                                    f"At least {record['minimumApprovedPercent']}% of current source strings must be approved"
                                )
                        shutil.copytree(source, snapshot / locale)
                        catalog.save(
                            str(snapshot / locale / catalog_path.relative_to(source))
                        )
                        if font_only:
                            resource_map["strings"]["lang"] = coverage_language
                            (snapshot / locale / commands.LANG_MAP).write_text(
                                json.dumps(resource_map)
                            )
                        commands.LANG_ROOT = snapshot
                        report = check_lang(locale)
                        status["checks"] = report
                        reason = policy_tools.check_approval(
                            record, font_hash, report, custom_fonts=custom_fonts
                        )
                        if reason:
                            status["reasons"].append(reason)
                        fingerprint = digest(
                            (
                                fingerprint
                                + str(catalog)
                                + (record.get("coverageLanguage") or "")
                            ).encode()
                        )
                except (OSError, ValueError, KeyError, TypeError) as error:
                    status["reasons"].append(str(error))
                finally:
                    commands.LANG_ROOT = root
                if status["reasons"]:
                    if prior:
                        shutil.copyfile(previous / path.name, path)
                        languages.append(
                            {
                                **prior,
                                "url": f"https://github.com/{repository}/releases/download/{tag}/{path.name}",
                            }
                        )
                        status["retainedVersion"] = prior["version"]
                    continue
                status["status"] = "ready"
                commands.LANG_ROOT = snapshot
                try:
                    if not prior or prior["inputHash"] != fingerprint or rebuild:
                        commands.pack_lang(locale, output, version=1)
                finally:
                    commands.LANG_ROOT = root
            if prior and prior["inputHash"] == fingerprint and not rebuild:
                shutil.copyfile(previous / path.name, path)
                version, updated_at = prior["version"], prior["updatedAt"]
                content_hash = prior["contentHash"]
            else:
                normalized = stamp_version(path.read_bytes(), 1)
                content_hash = digest(normalized)
                if prior and content_hash == prior["contentHash"]:
                    path.write_bytes(original)
                    version, updated_at = prior["version"], prior["updatedAt"]
                else:
                    version = (
                        max(versions.get(locale, 0), prior["version"] if prior else 0)
                        + 1
                    )
                    path.write_bytes(stamp_version(normalized, version))
            versions[locale] = max(versions.get(locale, 0), version)
            data = path.read_bytes()
            name = re.sub(
                r"\s*<[^>]*>\s*$", "", catalog.metadata.get("Language-Team", locale)
            ).strip()
            languages.append(
                {
                    "locale": locale,
                    "name": name or locale,
                    "nativeName": catalog.metadata.get("Name") or name or locale,
                    "version": version,
                    "updatedAt": updated_at,
                    "url": f"https://github.com/{repository}/releases/download/{tag}/{path.name}",
                    "sha256": digest(data),
                    "size": len(data),
                    **(
                        {"translatedStrings": 0, "totalStrings": 0}
                        if font_only
                        else completion(catalog, template)
                    ),
                    # A component link also works for locales Weblate normalizes to short codes.
                    "translationUrl": "https://translate.repebble.com/projects/pebbleos/watch/",
                    "inputHash": fingerprint,
                    "contentHash": content_hash,
                }
            )
    finally:
        commands.LANG_ROOT = previous_root
    manifest = {
        "schemaVersion": 1,
        "release": tag,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "sourceCommit": git(root, "rev-parse", "HEAD"),
        "languages": languages,
        "versionHighWater": versions,
    }

    # Release URLs and publication metadata naturally change between snapshots.
    def comparable(entries):
        return [{k: v for k, v in e.items() if k != "url"} for e in entries]

    changed = (
        bool(languages)
        if not old
        else comparable(languages) != comparable(old["languages"])
    )
    (output / "readiness.json").write_text(
        json.dumps({"languages": readiness}, indent=2, ensure_ascii=False) + "\n"
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    return manifest, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default="coredevices/pebbleos-translations")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    _, changed = build(
        args.root,
        args.output,
        tag=args.tag,
        repository=args.repository,
        previous=args.previous,
        rebuild=args.rebuild,
    )
    print("Release contents changed" if changed else "No release changes")


if __name__ == "__main__":
    main()
