# SPDX-FileCopyrightText: 2024 Google LLC
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os

import polib

if __package__:
    from .language_characters import language_characters, with_shaping_forms
else:
    from language_characters import language_characters, with_shaping_forms


def uses_emoji_font(cp):
    # codepoint_is_emoji() in the firmware revision recorded by the snapshot.
    return cp in {
        0x2192,
        0x25AA,
        0x25AB,
        0x25B6,
        0x25BA,
        0x25C0,
        0x25FB,
        0x25FC,
        0x25FD,
        0x25FE,
    } or any(
        low <= cp <= high
        for low, high in (
            (0x1F300, 0x1FAFF),
            (0x2300, 0x23FF),
            (0x2600, 0x27BF),
            (0x2B00, 0x2BFF),
            (0x1F100, 0x1F2FF),
        )
    )


def generate_codepoint_requirements(
    path, encoding="utf-8", controlchars=False, language=None
):
    catalog = polib.pofile(str(path), encoding=encoding)
    language = language or catalog.metadata.get("Language")
    baseline_locale, codepoints = language_characters(language)
    for entry in catalog:
        if entry.obsolete or not entry.translated():
            continue
        texts = entry.msgstr_plural.values() if entry.msgid_plural else [entry.msgstr]
        for text in texts:
            codepoints.update(ord(character) for character in text)
    return {
        "language": language,
        "baseline_locale": baseline_locale,
        "codepoints": sorted(
            cp
            for cp in with_shaping_forms(codepoints)
            if not uses_emoji_font(cp)
            and (chr(cp).isprintable() or (cp < 0x20 and controlchars))
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Given a PO file, generate a JSON file containing the codepoints required to display the translated strings"
    )
    parser.add_argument(
        "--language", help="Selected language (overrides the PO header)"
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
                    args.input, args.encoding, args.controlchars, language=args.language
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
