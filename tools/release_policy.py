# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Read Weblate publication evidence and verify reviewed font inputs."""

import hashlib
import json
import os
import re
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import polib

if __package__:
    from . import lang_commands as commands
    from .lang_check import load_builtin_coverage
    from .pack_format import FONT_SLOTS, SPECIALIZED_FONT_SLOTS, TEXT_FONT_SLOTS
else:
    import lang_commands as commands
    from lang_check import load_builtin_coverage
    from pack_format import FONT_SLOTS, SPECIALIZED_FONT_SLOTS, TEXT_FONT_SLOTS

WEBLATE_ORIGIN = "https://translate.repebble.com"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def asset(source, name):
    if not isinstance(name, str) or not name:
        raise ValueError("A font asset needs a filename")
    path = (source / name).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ValueError("Font assets must stay in the language folder")
    return path


def has_custom_fonts(mapping):
    commands.validate_map(mapping)
    return any(
        entry["file"] for entry in commands.resolve_font_entries(mapping).values()
    )


def font_inputs(source, mapping):
    """Cover aliases, rendering/subsetting options, license bytes and base fonts."""
    commands.validate_map(mapping)
    files = {}
    licenses = sorted(source.glob("LICENSE*"))
    for entry in commands.resolve_font_entries(mapping).values():
        if not entry.get("file"):
            continue
        license_name = entry.get("license")
        if not license_name and len(licenses) == 1:
            license_name = licenses[0].name
        if not license_name:
            raise ValueError("Every custom font needs an identifiable license")
        for name in (entry["file"], license_name, entry.get("characterList")):
            if name:
                data = asset(source, name).read_bytes()
                if not data:
                    raise ValueError(f"Font or license asset is empty: {name}")
                files[name] = digest(data)
    inputs = {
        "fonts": mapping["fonts"],
        "files": files,
        "builtInCoverage": digest(
            json.dumps(load_builtin_coverage(), sort_keys=True).encode()
        ),
    }
    return digest(json.dumps(inputs, sort_keys=True).encode())


def gaps(report):
    # Also filter older draft reports that compared specialized styles to the alphabet.
    return {
        font["slot"]: sorted(
            {
                int(item["codepoint"].replace("U+", "0x"), 0)
                for item in font["uncovered_characters"]
            }
        )
        for font in report["fonts"]
        if font["slot"] not in SPECIALIZED_FONT_SLOTS and font["uncovered_characters"]
    }


def check_approval(record, fingerprint, report, *, custom_fonts=True):
    if not report["ok"]:
        return "Catalog or font compilation checks failed"
    if not custom_fonts:
        if any(slot in TEXT_FONT_SLOTS for slot in gaps(report)):
            return "Built-in text fonts are missing required characters; supply a custom font"
        return None
    approval = record.get("fontApproval") or {}
    if (
        not approval.get("approvedBy")
        or approval.get("inputHash") != fingerprint
        or approval.get("redistributionConfirmed") is not True
        or approval.get("renderingReviewed") is not True
        or not isinstance(approval.get("note"), str)
        or not approval["note"].strip()
    ):
        return "Font/license/rendering approval is missing or out of date"
    accepted = approval.get("acceptedMissingCharacters", {})
    if not isinstance(accepted, dict) or any(
        slot not in FONT_SLOTS
        or not isinstance(points, list)
        or any(type(cp) is not int or not 0 <= cp <= 0x10FFFF for cp in points)
        for slot, points in accepted.items()
    ):
        return "Invalid coverage acceptance record"
    if any(
        set(points) - set(accepted.get(slot, []))
        for slot, points in gaps(report).items()
    ):
        return "New font coverage gaps require maintainer approval"
    if any(i["code"] == "language_baseline_unknown" for i in report["issues"]) and (
        approval.get("unknownBaselineAccepted") is not True
    ):
        return "Unknown language baseline requires maintainer acknowledgement"
    return None


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Weblate export redirected; refusing to forward credentials")


def release_evidence(locale):
    """Read publication settings, native reviewers and font approval from Weblate."""
    if not isinstance(locale, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_@.-]*", locale
    ):
        raise ValueError("Invalid pack locale")
    token = os.environ.get("WEBLATE_API_TOKEN")
    if not token:
        raise ValueError(
            "WEBLATE_API_TOKEN is required to verify publication readiness"
        )
    request = Request(
        f"{WEBLATE_ORIGIN}/api/pebble/release/{quote(locale)}/",
        headers={
            "Authorization": f"Token {token}",
            # Cloudflare blocks urllib's default user agent before authentication.
            "User-Agent": "pebble-language-pack-publisher/0.1",
        },
    )
    with build_opener(NoRedirects).open(request, timeout=30) as response:
        data = response.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Approved-string export exceeds 10 MiB")
    evidence = json.loads(data)
    validate_evidence(evidence, locale)
    return evidence


def validate_evidence(evidence, locale):
    if (
        evidence.get("schemaVersion") != 2
        or evidence.get("component") != "pebbleos/watch"
        or evidence.get("locale") != locale
        or evidence.get("publicationEnabled") is not True
        or evidence.get("reviewEnabled") is not True
        or evidence.get("approvedOnlyCommits") is not True
    ):
        raise ValueError("Enable publication checks and native reviews in Weblate")
    minimum = evidence.get("minimumApprovedPercent")
    if type(minimum) is not int or not 80 <= minimum <= 100:
        raise ValueError("Approval threshold must be between 80 and 100 percent")
    if evidence.get("kind") == "font-only":
        if not re.match(r"(?i)^en[_@-]", locale) or not evidence.get(
            "coverageLanguage"
        ):
            raise ValueError("Only English font packs can use font-only publication")
    elif evidence.get("kind") == "translation":
        if not isinstance(evidence.get("reviewers"), list) or not evidence["reviewers"]:
            raise ValueError(
                "Community language: assign a language reviewer in Weblate"
            )
        if not isinstance(evidence.get("language"), str):
            raise ValueError("Weblate returned no native language")
        catalog = polib.pofile(evidence["catalog"], check_for_duplicates=True)
        if catalog.metadata.get("Language") != evidence["language"]:
            raise ValueError("Weblate returned a catalog for a different language")
    else:
        raise ValueError("Unknown publication language type")


def release_catalog(original, template, approved):
    """Preserve pack headers but take targets only from the approved export."""
    result = polib.POFile()
    result.metadata = dict(original.metadata)
    if approved.metadata.get("Plural-Forms"):
        result.metadata["Plural-Forms"] = approved.metadata["Plural-Forms"]
    targets = {(e.msgctxt, e.msgid): e for e in approved if not e.obsolete}
    committed = {(e.msgctxt, e.msgid): e for e in original if not e.obsolete}
    for source in template:
        if source.obsolete:
            continue
        target = targets.get((source.msgctxt, source.msgid))
        if (
            target
            and target.translated()
            and target.msgid_plural == source.msgid_plural
        ):
            saved = committed.get((source.msgctxt, source.msgid))
            if (
                not saved
                or saved.fuzzy
                or saved.msgid_plural != target.msgid_plural
                or saved.msgstr != target.msgstr
                or dict(saved.msgstr_plural) != dict(target.msgstr_plural)
            ):
                raise ValueError(
                    "Approved translations have not been synchronized to Git"
                )
            # Source flags are authoritative for placeholder checks.
            result.append(
                polib.POEntry(
                    msgid=source.msgid,
                    msgctxt=source.msgctxt,
                    msgid_plural=source.msgid_plural,
                    msgstr=target.msgstr,
                    msgstr_plural=dict(target.msgstr_plural),
                    flags=list(source.flags),
                )
            )
    return result
