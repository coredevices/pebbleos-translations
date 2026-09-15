# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Read-only, service-facing validation of a language and its uploaded fonts."""

import json
import re
import struct
import tempfile
from pathlib import Path

import lang_commands as commands
import polib
from extract_builtin_coverage import pbf_codepoints
from generate_codepoint_requirements import generate_codepoint_requirements
from pack_format import FONT_SLOTS, serialize

COVERAGE_PATH = Path(__file__).resolve().parents[1] / "data/builtin_font_coverage.json"


def load_builtin_coverage():
    snapshot = json.loads(COVERAGE_PATH.read_text())
    if snapshot["schema_version"] != 1 or set(snapshot["fonts"]) != set(FONT_SLOTS):
        raise ValueError("Unsupported or incomplete built-in font coverage snapshot")
    for font in snapshot["fonts"].values():
        if not isinstance(font["codepoints"], list) or any(
            type(cp) is not int or not 0 <= cp <= 0x10FFFF for cp in font["codepoints"]
        ):
            raise ValueError("Invalid built-in font codepoints")
    return snapshot


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


def translated_texts(catalog):
    for entry in catalog:
        if entry.obsolete or not entry.translated():
            continue
        for text in (
            [entry.msgstr] if not entry.msgid_plural else entry.msgstr_plural.values()
        ):
            yield entry, text


def check_lang(lang):
    """Return schema v1 diagnostics; success means buildable, not release approval."""
    report = {
        "schema_version": 1,
        "language": lang,
        "ok": False,
        "built_in_coverage": "not_checked",
        "progress": None,
        "fonts": [],
        "issues": [],
    }

    def issue(severity, code, message, **details):
        report["issues"].append(
            {"severity": severity, "code": code, "message": message, **details}
        )

    try:
        snapshot = load_builtin_coverage()
        report["built_in_coverage"] = "checked"
        report["built_in_coverage_revision"] = snapshot["source"]["revision"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        issue(
            "error",
            "built_in_coverage_invalid",
            f"Cannot load built-in font coverage: {error}",
        )
        return report
    try:
        source = commands.lang_dir(lang)
        resource_map = json.loads((source / commands.LANG_MAP).read_text())
        commands.validate_map(resource_map)
    except (OSError, ValueError, KeyError, TypeError) as error:
        issue("error", "resource_map_invalid", str(error))
        return report

    if (source / commands.INCOMPLETE).is_file():
        issue(
            "error",
            "language_incomplete",
            f"Locale {lang} is marked incomplete; resolve the outstanding work before building a pack.",
        )

    with tempfile.TemporaryDirectory(prefix="check-lang-") as directory:
        temp = Path(directory)
        resources = {"STRINGS": b""}
        codepoints = None
        examples = {}
        if resource_map["strings"]["file"]:
            po = source / resource_map["strings"]["file"]
            try:
                catalog = polib.pofile(str(po), check_for_duplicates=True)
                active = [entry for entry in catalog if not entry.obsolete]
                fuzzy = sum("fuzzy" in entry.flags for entry in active)
                translated = sum(entry.translated() for entry in active)
                report["progress"] = {
                    "total": len(active),
                    "translated": translated,
                    "needs_review": fuzzy,
                    "untranslated": len(active) - translated - fuzzy,
                }
                for entry, text in translated_texts(catalog):
                    for character in text:
                        if character.isprintable():
                            examples.setdefault(
                                ord(character),
                                {
                                    "source": entry.msgid,
                                    "translation": text,
                                    "context": entry.msgctxt,
                                    "line": entry.linenum,
                                },
                            )
                if fuzzy or translated < len(active):
                    issue(
                        "warning",
                        "translation_incomplete",
                        "Some strings are untranslated or need review. Fuzzy entries are excluded from the pack.",
                    )
                mo = temp / "strings.mo"
                warnings = commands.compile_catalog(po, mo)
                if warnings:
                    issue("warning", "catalog_warning", warnings)
                resources["STRINGS"] = mo.read_bytes()
                codepoints = temp / "codepoints.json"
                codepoints.write_text(json.dumps(generate_codepoint_requirements(po)))
            except (OSError, ValueError, KeyError, TypeError) as error:
                issue("error", "catalog_invalid", str(error), file=str(po))

        compiled = {}
        for slot, entry in commands.resolve_font_entries(resource_map).items():
            name = entry["name"]
            if name not in compiled:
                result = {"slot": name, "file": entry["file"], "status": "base_font"}
                data = b""
                available = set()
                if entry["file"]:
                    try:
                        font = commands.configure_font(source, entry, codepoints)
                        available = {cp for cp, glyph in font.face.get_chars() if glyph}
                        data = commands.build_font(source, entry, codepoints, font=font)
                        result.update(status="checked")
                        result["size_bytes"] = len(data)
                    except (
                        OSError,
                        ValueError,
                        RuntimeError,
                        KeyError,
                        TypeError,
                        OverflowError,
                        re.error,
                    ) as error:
                        result["status"] = "error"
                        issue(
                            "error",
                            "font_invalid",
                            str(error),
                            slot=slot,
                            file=entry["file"],
                        )
                compiled[name] = data, result, available
            data, result, available = compiled[name]
            resources[slot] = data
            base = set(snapshot["fonts"][slot]["codepoints"])
            uploaded = set(pbf_codepoints(data)) if data else set()

            def details(codepoints):
                return [
                    {
                        "character": chr(cp),
                        "codepoint": f"U+{cp:04X}",
                        "example": examples[cp],
                    }
                    for cp in sorted(codepoints)
                ]

            required = {cp for cp in examples if not uses_emoji_font(cp)}
            base_missing = required - base
            uncovered = base_missing - uploaded
            report["fonts"].append(
                {
                    **result,
                    "slot": slot,
                    "resolved_slot": name,
                    "built_in_missing_characters": details(base_missing),
                    "uncovered_characters": details(uncovered),
                    "missing_characters": details(uncovered - available)
                    if entry["file"]
                    else [],
                    "excluded_characters": details(uncovered & available)
                    if entry["file"]
                    else [],
                }
            )

        gaps = [font for font in report["fonts"] if font["uncovered_characters"]]
        if gaps:
            issue(
                "warning",
                "font_coverage_gap",
                "Some translated characters are absent from a slot's base font and built extension. "
                "Supply a font containing them or adjust its character selection if those strings use that slot. "
                "Numeric and unit-only slots intentionally have limited coverage; layout is not checked.",
                slots=[font["slot"] for font in gaps],
            )

        if not any(item["severity"] == "error" for item in report["issues"]):
            try:
                data = serialize(
                    [resources["STRINGS"]] + [resources[slot] for slot in FONT_SLOTS]
                )
                report["pack_size_bytes"] = len(data)
                report["ok"] = True
            except (ValueError, OverflowError, struct.error) as error:
                issue("error", "pack_invalid", str(error))
    return report


def format_report(report):
    lines = [
        f"{report['language']}: {'build checks passed' if report['ok'] else 'build checks failed'}"
    ]
    progress = report["progress"]
    if progress:
        lines.append(
            f"Translations: {progress['translated']}/{progress['total']}; "
            f"{progress['needs_review']} need review; {progress['untranslated']} untranslated."
        )
    for issue in report["issues"]:
        lines.append(
            f"{issue['severity'].upper()} [{issue['code']}]: {issue['message']}"
        )
    if report["built_in_coverage"] == "checked":
        lines.append(
            f"Built-in coverage: checked against {report['built_in_coverage_revision'][:12]}."
        )
    # Show a few distinct examples; the JSON retains every character per slot.
    shown = set()
    for font in report["fonts"]:
        for detail in font.get("uncovered_characters", []):
            if detail["codepoint"] not in shown and len(shown) < 5:
                shown.add(detail["codepoint"])
                lines.append(
                    f"  {font['slot']}: uncovered {detail['character']!r} ({detail['codepoint']}) "
                    f"in {detail['example']['translation']!r}"
                )
    if any(font.get("uncovered_characters") for font in report["fonts"]):
        lines.append("  See --json for coverage gaps in each font slot.")
    return "\n".join(lines)
