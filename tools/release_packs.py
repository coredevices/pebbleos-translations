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
from datetime import datetime, timezone
from pathlib import Path

import polib

if __package__:
    from . import lang_commands as commands
    from .pack_format import FONT_SLOTS, TABLE_SIZE, serialize
else:
    import lang_commands as commands
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
    resources[0] = catalog.to_binary()
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


def build(root, output, *, tag, repository, previous=None, rebuild=False):
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
    languages = []
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
            resource_map = json.loads((source / commands.LANG_MAP).read_text())
            catalog = polib.pofile(str(source / resource_map["strings"]["file"]))
            fingerprint, updated_at = input_state(root, locale)
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
            if prior and prior["inputHash"] == fingerprint and not rebuild:
                shutil.copyfile(previous / path.name, path)
                version, updated_at = prior["version"], prior["updatedAt"]
                content_hash = prior["contentHash"]
            else:
                commands.pack_lang(locale, output, version=1)
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
                    **completion(catalog, template),
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

    changed = not old or comparable(languages) != comparable(old["languages"])
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
