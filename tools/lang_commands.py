# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Catalog maintenance and universal language-pack builds."""

import json
import re
import struct
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path

import polib

# Support both the installed package and the existing direct-script commands.
if __package__:
    from .generate_codepoint_requirements import generate_codepoint_requirements
    from .pack_format import FONT_SLOTS, MAX_GLYPH_SIZE, serialize
else:
    from generate_codepoint_requirements import generate_codepoint_requirements
    from pack_format import FONT_SLOTS, MAX_GLYPH_SIZE, serialize

DATA_ROOT = (
    files("pebble_language_tools.data")
    if __package__
    else Path(__file__).resolve().parents[1] / "data"
)

LANG_ROOT = Path.cwd() if __package__ else Path(__file__).resolve().parent.parent
LANG_MAP = "lang_map.json"
CATALOG = "tintin.po"


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
    if __package__:
        from .fontgen import MAX_GLYPHS, MAX_GLYPHS_EXTENDED, Font
    else:
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
    if codepoints is not None:
        requirements = set(json.loads(Path(codepoints).read_text())["codepoints"])
        # Keep explicit legacy additions, but never let a hand-written subset exclude requirements.
        if character_list is not None:
            requirements.update(font.codepoints)
        if extended:
            snapshot = json.loads(
                (DATA_ROOT / "builtin_font_coverage.json").read_text()
            )
            requirements -= set(snapshot["fonts"][name]["codepoints"])
        font.codepoints = requirements
        font.regex = None
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


class FirmwareCatalog(polib.POFile):
    def ordered_metadata(self):
        # The watch reads only the first 399 bytes of the gettext header.
        required = ("Project-Id-Version", "Language", "Name")
        return [(key, self.metadata[key]) for key in required] + [
            item for item in super().ordered_metadata() if item[0] not in required
        ]


def firmware_catalog(catalog, *, locale=None, version=None):
    """Prepare binary-pack headers without changing the source PO or its strings."""
    result = FirmwareCatalog()
    result.extend(catalog)
    result.metadata = dict(catalog.metadata)
    raw_version = result.metadata.get("Project-Id-Version", "").strip()
    numeric = re.fullmatch(r"([1-9][0-9]*)(?:\.[0-9]+)?", raw_version)
    if version is not None:
        if not 1 <= version <= 65535:
            raise ValueError("Pack version must be between 1 and 65535")
        raw_version = str(version)
    elif not numeric or int(numeric[1]) > 65535 or len(raw_version) > 9:
        # New Weblate catalogs inherit "PACKAGE VERSION" from the POT.
        raw_version = "1"
    result.metadata["Project-Id-Version"] = raw_version
    result.metadata["Language"] = result.metadata.get("Language") or locale
    if not result.metadata["Language"]:
        raise ValueError("A language pack requires a Language header or locale")
    name = (
        result.metadata.get("Name")
        or re.sub(
            r"\s*<[^>]*>\s*$", "", result.metadata.get("Language-Team", "")
        ).strip()
        or result.metadata["Language"]
    )
    # LOCALE_NAME_LENGTH is 30 including the terminating NUL. Keep UTF-8 whole.
    result.metadata["Name"] = (
        name.splitlines()[0].encode("utf-8")[:29].decode("utf-8", errors="ignore")
    )
    return result


def pack_lang(lang, output, *, version=None):
    source = lang_dir(lang)
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
            # Do not let reserialization conceal malformed source syntax.
            compile_catalog(po, mo)
            catalog = firmware_catalog(
                polib.pofile(str(po)), locale=lang, version=version
            )
            compilation_source = temp / "firmware.po"
            catalog.save(str(compilation_source))
            compile_catalog(compilation_source, mo)
            resources["STRINGS"] = mo.read_bytes()
            codepoints = temp / "codepoints.json"
            codepoints.write_text(
                json.dumps(
                    generate_codepoint_requirements(
                        po, language=resource_map["strings"].get("lang") or lang
                    )
                )
            )

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
        if source.is_dir() and (source / LANG_MAP).is_file():
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
