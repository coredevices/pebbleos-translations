# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Catalog maintenance and universal language-pack builds."""

import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

from generate_codepoint_requirements import generate_codepoint_requirements
from pack_format import FONT_SLOTS, MAX_GLYPH_SIZE, serialize

LANG_ROOT = Path(__file__).resolve().parent.parent
LANG_MAP = "lang_map.json"
CATALOG = "tintin.po"
INCOMPLETE = "INCOMPLETE"


def lang_dir(lang):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_@.-]*", lang):
        raise ValueError(f"Invalid locale identifier: {lang!r}")
    return LANG_ROOT / lang


def new_map(lang):
    return {
        "strings": {"lang": lang, "name": "STRINGS", "file": CATALOG},
        "fonts": [{"name": name, "file": ""} for name in FONT_SLOTS],
        "images": [],
    }


def configure_font(source, entry, codepoints):
    from fontgen import MAX_GLYPHS, MAX_GLYPHS_EXTENDED, Font

    name = entry["name"]
    name_height = int(re.search(r"\d+", name).group())
    extended = bool(entry.get("extended", True))
    font = Font(
        str(source / entry["file"]),
        entry.get("pixelHeight") or name_height,
        MAX_GLYPHS_EXTENDED if extended else MAX_GLYPHS,
        MAX_GLYPH_SIZE,
        entry.get("compatibility") == "2.7",
        baseline=name_height if extended else None,
    )
    if entry.get("characterRegex") is not None:
        font.set_regex_filter(entry["characterRegex"])
    character_list = entry.get("characterList")
    if character_list is not None:
        font.set_codepoint_list(source / character_list)
    elif codepoints is not None:
        font.set_codepoint_list(codepoints)
    if entry.get("compress"):
        font.set_compression(entry["compress"])
    if entry.get("trackingAdjust") is not None:
        font.set_tracking_adjust(entry["trackingAdjust"])
    return font


def build_font(source, entry, codepoints, *, font=None):
    font = font or configure_font(source, entry, codepoints)
    try:
        font.build_tables()
        return font.bitstring()
    except (ValueError, RuntimeError, struct.error) as error:
        raise ValueError(
            f"{entry['name']} ({entry['file']}): {error}. "
            "Adjust the font or its pixelHeight; packs must fit all supported watches."
        ) from error


def validate_map(resource_map):
    if not isinstance(resource_map, dict):
        raise TypeError("The resource map must be an object")
    if not isinstance(resource_map.get("strings"), dict) or not isinstance(
        resource_map.get("fonts"), list
    ):
        raise TypeError("The resource map needs strings and fonts entries")
    if not isinstance(resource_map["strings"].get("file"), str):
        raise TypeError("The catalog file must be a filename or an empty string")
    if resource_map["strings"]["name"] != "STRINGS":
        raise ValueError("The catalog resource must be named STRINGS")
    for entry in resource_map["fonts"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise TypeError("Each font entry needs a slot name")
        if "alias" in entry:
            if not isinstance(entry["alias"], str):
                raise ValueError("Font aliases must name a font slot")
        elif not isinstance(entry.get("file"), str):
            raise ValueError(
                f"{entry['name']}: supply a font filename or an empty string"
            )
    names = [entry["name"] for entry in resource_map["fonts"]]
    if len(names) != len(set(names)) or not set(names).issubset(FONT_SLOTS):
        raise ValueError("lang_map.json contains duplicate or unknown font slots")
    if resource_map.get("images"):
        raise ValueError("Language packs do not support image resources")
    resolve_font_entries(resource_map)


def resolve_font_entries(resource_map):
    entries = {name: {"name": name, "file": ""} for name in FONT_SLOTS}
    entries.update({entry["name"]: entry for entry in resource_map["fonts"]})

    def resolve(name, visiting):
        if name not in entries or name in visiting:
            raise ValueError(f"Unknown or cyclic font alias: {name}")
        entry = entries[name]
        if "alias" in entry:
            return resolve(entry["alias"], visiting | {name})
        return entry

    return {name: resolve(name, set()) for name in FONT_SLOTS}


def compile_catalog(po, mo):
    result = subprocess.run(
        ["msgfmt", "-c", "-o", str(mo), str(po)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Catalog compilation failed")
    return result.stderr.strip()


def pack_lang(lang, output):
    source = lang_dir(lang)
    if (source / INCOMPLETE).is_file():
        raise ValueError(f"Locale {lang} is marked incomplete")
    resource_map = json.loads((source / LANG_MAP).read_text())
    validate_map(resource_map)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{lang}-") as temp:
        temp = Path(temp)
        strings = resource_map["strings"]
        resources = {"STRINGS": b""}
        codepoints = None
        if strings["file"]:
            po = source / strings["file"]
            mo = temp / "strings.mo"
            compile_catalog(po, mo)
            resources["STRINGS"] = mo.read_bytes()
            codepoints = temp / "codepoints.json"
            codepoints.write_text(json.dumps(generate_codepoint_requirements(po)))

        entries = {name: {"name": name, "file": ""} for name in FONT_SLOTS}
        entries.update({entry["name"]: entry for entry in resource_map["fonts"]})

        def resolve(name, visiting):
            if name in resources:
                return resources[name]
            if name not in entries or name in visiting:
                raise ValueError(f"Unknown or cyclic font alias: {name}")
            entry = entries[name]
            if "alias" in entry:
                if entry["alias"] not in entries:
                    raise ValueError(f"Unknown font alias: {entry['alias']}")
                data = resolve(entry["alias"], visiting | {name})
            elif not entry["file"]:
                data = b""
            else:
                data = build_font(source, entry, codepoints)
            resources[name] = data
            return data

        data = serialize(
            [resources["STRINGS"]] + [resolve(name, set()) for name in FONT_SLOTS]
        )
        path = output / f"{lang}.pbl"
        path.write_bytes(data)
    print(f"Created {path}")
    return path


def pack_all_langs(output):
    for source in sorted(LANG_ROOT.iterdir()):
        if (
            source.is_dir()
            and (source / LANG_MAP).is_file()
            and not (source / INCOMPLETE).is_file()
        ):
            pack_lang(source.name, output)


def make_lang(lang, pot):
    pot = Path(pot).resolve()
    if not pot.is_file():
        raise ValueError(f"Source catalog does not exist: {pot}")
    source = lang_dir(lang)
    source.mkdir(exist_ok=True)
    resource_map = source / LANG_MAP
    if not resource_map.exists():
        resource_map.write_text(json.dumps(new_map(lang), indent=4) + "\n")
    catalog = source / CATALOG
    if catalog.exists():
        command = [
            "msgmerge",
            f"--lang={lang}",
            "--update",
            "--backup=none",
            str(catalog),
            str(pot),
        ]
    else:
        command = [
            "msginit",
            "-l",
            lang,
            "--no-translator",
            "-i",
            str(pot),
            "-o",
            str(catalog),
        ]
    subprocess.run(command, check=True)
