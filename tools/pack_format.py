# SPDX-FileCopyrightText: 2024 Google LLC
# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Language-pack serialization, adapted from PebbleOS pbpack and stm32_crc."""

import struct

# Resource IDs are positional; keep this order compatible with the loader.
FONT_SLOTS = (
    "GOTHIC_14_EXTENDED",
    "GOTHIC_14_BOLD_EXTENDED",
    "GOTHIC_18_EXTENDED",
    "GOTHIC_18_BOLD_EXTENDED",
    "GOTHIC_24_EXTENDED",
    "GOTHIC_24_BOLD_EXTENDED",
    "GOTHIC_28_EXTENDED",
    "GOTHIC_28_BOLD_EXTENDED",
    "GOTHIC_36_EXTENDED",
    "GOTHIC_36_BOLD_EXTENDED",
    "BITHAM_18_LIGHT_SUBSET_EXTENDED",
    "BITHAM_30_BLACK_EXTENDED",
    "BITHAM_34_LIGHT_SUBSET_EXTENDED",
    "BITHAM_34_MEDIUM_NUMBERS_EXTENDED",
    "BITHAM_42_BOLD_EXTENDED",
    "BITHAM_42_LIGHT_EXTENDED",
    "BITHAM_42_MEDIUM_NUMBERS_EXTENDED",
    "ROBOTO_CONDENSED_21_EXTENDED",
    "ROBOTO_BOLD_SUBSET_49_EXTENDED",
    "DROID_SERIF_28_BOLD_EXTENDED",
)
# Firmware uses these for digits or short units, rather than arbitrary text.
SPECIALIZED_FONT_SLOTS = frozenset(
    slot for slot in FONT_SLOTS if "NUMBERS" in slot or "SUBSET" in slot
)
TABLE_SIZE = 256
MAX_GLYPH_SIZE = 256  # Smallest glyph buffer across supported watches.


def crc32(data):
    crc = 0xFFFFFFFF
    for start in range(0, len(data), 4):
        word = data[start : start + 4]
        if len(word) < 4:
            word = word[::-1] + b"\0" * (4 - len(word))
        for byte in reversed(word):
            crc ^= byte << 24
            for _ in range(8):
                crc = (
                    (crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)
                ) & 0xFFFFFFFF
    return crc


def serialize(resources):
    if len(resources) != 1 + len(FONT_SLOTS):
        raise ValueError("A language pack must contain strings and all 20 font slots")
    # Assign duplicate data at its last occurrence: the loader derives total
    # length from the final table entry, which must end at the end of the pack.
    contents = list(dict.fromkeys(reversed(resources)))[::-1]
    offsets = {}
    offset = 0
    for content in contents:
        offsets[content] = offset
        offset += len(content)
    body = b"".join(contents)
    table = b"".join(
        struct.pack("<IIII", i, offsets[content], len(content), crc32(content))
        for i, content in enumerate(resources, 1)
    )
    table += b"\0" * ((TABLE_SIZE - len(resources)) * 16)
    return struct.pack("<III", len(resources), crc32(body), 0) + table + body
