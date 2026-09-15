# SPDX-FileCopyrightText: 2024 Google LLC
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os

import polib


def generate_codepoint_requirements(path, encoding="utf-8", controlchars=False):
    # Preserve the existing extended-font subset policy. This is not a claim
    # about built-in glyph coverage; check_lang inspects all translated text.
    catalog = polib.pofile(str(path), encoding=encoding)
    codepoints = set()
    for entry in catalog:
        if entry.obsolete or not entry.translated():
            continue
        texts = entry.msgstr_plural.values() if entry.msgid_plural else [entry.msgstr]
        for text in texts:
            codepoints.update(ord(character) for character in text)
    return {
        "language": catalog.metadata.get("Language"),
        "codepoints": sorted(
            cp for cp in codepoints if cp > 0x2AF or (cp < 0x20 and controlchars)
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Given a PO file, generate a JSON file containing the codepoints required to display the translated strings"
    )
    parser.add_argument("input", help="Path to PO file containing translated strings")
    parser.add_argument(
        "--output", help="Path to output JSON file containing codepoints"
    )
    parser.add_argument(
        "--encoding",
        help="Set encoding of input file (default is utf-8)",
        default="utf-8",
    )
    parser.add_argument(
        "--controlchars",
        help="If set, control characters (U+0000 - U+001F) will not be excluded",
        action="store_true",
    )
    args = parser.parse_args()

    if args.output is None:
        args.output = os.path.splitext(args.input)[0] + ".json"

    with open(args.output, mode="w") as fout:
        fout.write(
            json.dumps(
                generate_codepoint_requirements(
                    args.input, args.encoding, args.controlchars
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
