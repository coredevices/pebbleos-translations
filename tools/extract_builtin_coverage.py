# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Maintainer-only extraction of normal-firmware font coverage from a Git revision."""

import argparse
import hashlib
import json
import posixpath
import re
import struct
import subprocess
import tempfile
from pathlib import Path

import freetype
from pack_format import FONT_SLOTS


def pbf_codepoints(data):
    """Read actual v2/v3 PBF glyph mappings, excluding the missing-glyph box."""
    version, _, count, wildcard, buckets, cp_bytes = struct.unpack_from("<BBHHBB", data)
    if version not in (2, 3) or cp_bytes not in (2, 4) or not buckets:
        raise ValueError("Unsupported PBF header")
    header_size, features = (data[8], data[9]) if version == 3 else (8, 0)
    if header_size != (10 if version == 3 else 8):
        raise ValueError("Unsupported PBF header size")
    entry_format = (
        "<" + ("I" if cp_bytes == 4 else "H") + ("H" if features & 1 else "I")
    )
    entry_size = struct.calcsize(entry_format)
    offsets_start = header_size + buckets * 4
    glyphs_start = offsets_start + count * entry_size
    mappings = {}
    for index in range(buckets):
        bucket, size, offset = struct.unpack_from("<BBH", data, header_size + index * 4)
        if bucket != index or offset + size * entry_size > count * entry_size:
            raise ValueError("Invalid PBF hash table")
        for i in range(size):
            cp, glyph = struct.unpack_from(
                entry_format, data, offsets_start + offset + i * entry_size
            )
            if cp in mappings or cp % buckets != bucket or cp > 0x10FFFF:
                raise ValueError("Invalid PBF codepoint table")
            if glyph and glyphs_start + glyph + 5 > len(data):
                raise ValueError("Invalid PBF glyph offset")
            mappings[cp] = glyph
    if len(mappings) != count:
        raise ValueError("PBF glyph count does not match its tables")
    return sorted(
        cp
        for cp, glyph in mappings.items()
        if glyph and cp != wildcard and glyph != mappings.get(wildcard)
    )


def extract(firmware, revision):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(firmware), *args])

    commit = git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    inputs = {}
    absent_overrides = []

    def read(path, visiting=frozenset()):
        if path in visiting:
            raise ValueError(f"Cyclic source symlink: {path}")
        data = git("show", f"{commit}:{path}")
        inputs[path] = hashlib.sha256(data).hexdigest()
        if git("ls-tree", commit, "--", path).startswith(b"120000 "):
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), data.decode())
            )
            if not git("ls-tree", commit, "--", target):
                absent_overrides.append(path)
                return b""
            return read(target, visiting | {path})
        return data

    base_maps = [
        "resources/common/base/resource_map.json",
        "resources/normal/base/resource_map.json",
    ]
    definitions = {}
    for path in base_maps:
        for entry in json.loads(read(path))["media"]:
            definitions.setdefault(entry["name"], {}).update(entry)
    names = {slot.removesuffix("_EXTENDED") for slot in FONT_SLOTS}
    paths = (
        git(
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "resources/common",
            "resources/normal",
        )
        .decode()
        .splitlines()
    )
    # Refuse to label base coverage universal if any board overrides these slots.
    for path in paths:
        if (
            path.endswith("/resource_map.json")
            and len(Path(path).parts) == 4
            and path not in base_maps
        ):
            data = read(path)
            if not data:  # Firmware find_node also ignores dangling board-map links.
                continue
            for entry in json.loads(data)["media"]:
                if (
                    entry["name"] in names
                    and {**definitions[entry["name"]], **entry}
                    != definitions[entry["name"]]
                ):
                    raise ValueError(
                        f"{path} overrides {entry['name']}; review universal coverage before refreshing"
                    )
    for path in (
        "tools/font/fontgen.py",
        "tools/resources/resource_map/resource_generator_font.py",
    ):
        read(path)

    fonts = {}
    for slot in FONT_SLOTS:
        name = slot.removesuffix("_EXTENDED")
        definition = definitions[name]
        path = "resources/" + definition["file"]
        data = read(path)
        if path.endswith(".pbf"):
            # Firmware copies PBF files verbatim; map regex/list fields do not filter them.
            codepoints = pbf_codepoints(data)
            method = "pbf_glyph_table"
        elif path.endswith((".ttf", ".otf")):
            with tempfile.TemporaryDirectory() as temp:
                font_path = Path(temp) / Path(path).name
                font_path.write_bytes(data)
                face = freetype.Face(str(font_path))
                available = {cp for cp, glyph in face.get_chars() if glyph}
            selected = range(0x20, 0x10FFFF)
            if definition.get("characterList"):
                selected = {
                    int(cp)
                    for cp in json.loads(
                        read("resources/" + definition["characterList"])
                    )["codepoints"]
                }
            regex = re.compile(definition.get("characterRegex", ".*"))
            codepoints = sorted(
                cp
                for cp in available
                if cp != 0x25AF
                and (cp == 0x2026 or (cp in selected and regex.match(chr(cp))))
            )
            method = "font_charmap_with_firmware_filters"
        else:
            raise ValueError(f"Unsupported font source: {path}")
        fonts[slot] = {
            "base_font": name,
            "file": path,
            "method": method,
            "codepoints": codepoints,
        }
    return {
        "schema_version": 1,
        "source": {
            "repository": "https://github.com/coredevices/pebbleos",
            "revision": commit,
            "variant": "normal",
            "absent_board_overrides": absent_overrides,
            "input_sha256": dict(sorted(inputs.items())),
        },
        "fonts": fonts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--firmware",
        required=True,
        type=Path,
        help="PebbleOS Git checkout (refresh only)",
    )
    parser.add_argument("--revision", required=True, help="Committed firmware revision")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = extract(args.firmware, args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n")


if __name__ == "__main__":
    main()
