# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Manage catalogs and build universal Pebble language packs."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lang_commands import make_lang, pack_all_langs, pack_lang


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    make = subparsers.add_parser("make_lang", help="Initialize or update a catalog")
    make.add_argument("--lang", required=True, help="Locale identifier")
    make.add_argument("--pot", required=True, type=Path, help="Current source catalog")
    check = subparsers.add_parser(
        "check_lang", help="Validate a language without writing a pack"
    )
    check.add_argument("--lang", required=True, help="Locale identifier")
    check.add_argument(
        "--json", action="store_true", help="Output structured diagnostics"
    )
    for command in ("pack_lang", "pack_all_langs"):
        pack = subparsers.add_parser(command, help="Build universal language packs")
        pack.add_argument("--output", type=Path, default=Path("dist"))
        if command == "pack_lang":
            pack.add_argument("--lang", required=True, help="Locale identifier")
    args = parser.parse_args()
    if args.command == "check_lang":
        from lang_check import check_lang, format_report

        report = check_lang(args.lang)
        print(
            json.dumps(report, ensure_ascii=False, indent=2)
            if args.json
            else format_report(report)
        )
        return 0 if report["ok"] else 1
    try:
        if args.command == "make_lang":
            make_lang(args.lang, args.pot)
        elif args.command == "pack_lang":
            pack_lang(args.lang, args.output)
        else:
            pack_all_langs(args.output)
    except (
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        KeyError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
