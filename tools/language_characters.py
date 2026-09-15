# SPDX-License-Identifier: Apache-2.0
"""Offline character requirements for the selected language."""

import json
import unicodedata
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

DATA_ROOT = (
    files("pebble_language_tools.data")
    if __package__
    else Path(__file__).resolve().parents[1] / "data"
)


@lru_cache(maxsize=1)
def snapshot():
    return json.loads((DATA_ROOT / "language_characters.json").read_text())["languages"]


def language_characters(language):
    locale = (language or "").split(".")[0].replace("_", "-")
    aliases = {"iw": "he", "in": "id", "ji": "yi"}
    locale = aliases.get(locale.lower(), locale)
    if "@latin" in locale.lower():
        locale = locale.split("@")[0].split("-")[0] + "-Latn"
    available = {key.lower(): key for key in snapshot()}
    while locale:
        if locale.lower() in available:
            key = available[locale.lower()]
            return key, set(snapshot()[key])
        locale = locale.rpartition("-")[0]
    return None, set()


@lru_cache(maxsize=1)
def shaping_forms():
    forms = {}
    for cp in range(0xFB50, 0xFF00):
        decomposition = unicodedata.decomposition(chr(cp)).split()
        if decomposition and decomposition[0] in (
            "<isolated>",
            "<initial>",
            "<medial>",
            "<final>",
        ):
            forms[cp] = {int(value, 16) for value in decomposition[1:]}
    return forms


def with_shaping_forms(codepoints):
    return set(codepoints) | {
        cp for cp, letters in shaping_forms().items() if letters <= codepoints
    }
