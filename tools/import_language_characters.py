# SPDX-License-Identifier: Apache-2.0
"""Import pinned CLDR exemplars using ICU's UnicodeSet parser (macOS maintainer tool)."""

import argparse
import ctypes as c
import hashlib
import json
import tarfile
import unicodedata as u
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    lib = c.CDLL("/usr/lib/libicucore.A.dylib")
    lib.uset_openPattern.argtypes = [
        c.POINTER(c.c_uint16),
        c.c_int32,
        c.POINTER(c.c_int32),
    ]
    lib.uset_openPattern.restype = c.c_void_p
    lib.uset_getItemCount.argtypes = [c.c_void_p]
    lib.uset_getItem.argtypes = [
        c.c_void_p,
        c.c_int32,
        c.POINTER(c.c_int32),
        c.POINTER(c.c_int32),
        c.POINTER(c.c_uint16),
        c.c_int32,
        c.POINTER(c.c_int32),
    ]
    lib.uset_close.argtypes = [c.c_void_p]

    def parse(pattern):
        raw = pattern.encode("utf-16-le")
        buf = (c.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)
        error = c.c_int32()
        handle = lib.uset_openPattern(buf, len(buf), c.byref(error))
        if error.value > 0:
            raise ValueError((pattern, error.value))
        result = []
        try:
            for i in range(lib.uset_getItemCount(handle)):
                start, end = c.c_int32(), c.c_int32()
                string = (c.c_uint16 * 256)()
                n = lib.uset_getItem(
                    handle, i, c.byref(start), c.byref(end), string, 256, c.byref(error)
                )
                if error.value > 0:
                    raise ValueError(error.value)
                if n:
                    result.append(bytes(string)[: n * 2].decode("utf-16-le"))
                else:
                    result.extend(chr(cp) for cp in range(start.value, end.value + 1))
        finally:
            lib.uset_close(handle)
        return result

    locales = {}
    with tarfile.open(args.archive) as archive:
        for member in archive.getmembers():
            if not member.name.endswith("/characters.json"):
                continue
            data = json.load(archive.extractfile(member))["main"]
            locale, info = next(iter(data.items()))
            characters = info["characters"]
            exemplars = parse(characters["exemplarCharacters"])
            required = set()
            for text in exemplars:
                upper = (
                    text.replace("i", "İ").upper()
                    if locale.split("-")[0] in ("tr", "az")
                    else text.upper()
                )
                for variant in (text, upper):
                    required.update(ord(ch) for ch in variant)
                    required.update(ord(ch) for ch in u.normalize("NFC", variant))
            required.update(
                ord(ch)
                for text in parse(characters.get("numbers", "[]"))
                for ch in text
                if u.category(ch) == "Nd"
            )
            locales[locale] = sorted(required)
        args.output.with_name("LICENSE.CLDR.txt").write_bytes(
            archive.extractfile("package/LICENSE").read()
        )
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {
                    "url": "https://registry.npmjs.org/cldr-misc-full/-/cldr-misc-full-48.0.0.tgz",
                    "version": "48.0.0",
                    "sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                    "policy": "main exemplars with uppercase and NFC variants, plus decimal digits",
                },
                "languages": locales,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    print(f"Imported {len(locales)} locales")


if __name__ == "__main__":
    main()
