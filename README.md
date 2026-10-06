# PebbleOS translations

Translation catalogs, fonts and tools for building universal language packs for
[PebbleOS](https://github.com/coredevices/pebbleos). The Weblate integration lives
in [Peblate](https://github.com/coredevices/peblate).

## Contributing

Use [Pebble translations](https://translate.repebble.com/) to translate, upload
fonts with their licenses, preview text and test draft packs. Language reviewers
approve wording; project maintainers manage font approval and publication in
Weblate. Keep translations concise and check that they fit in the watch preview.

Published updates require a language reviewer, at least 80% approved strings,
current font approval and passing build checks. Only approved strings ship.
Languages without reviewers remain community drafts; held updates retain their
previous published pack. English `en_*` font-only packs need font approval and
coverage checks but contain no translated strings.

## Local tools

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and GNU
gettext (`msgfmt`, `msginit` and `msgmerge` on `PATH`). Use `brew install gettext`
on macOS or install the `gettext` package on Debian/Ubuntu.

From this checkout:

```sh
uv sync --locked

# Check a language without changing its files
uv run --locked python tools/lang.py check_lang --lang de_DE

# Build one language or all languages
uv run --locked python tools/lang.py pack_lang --lang de_DE --output dist
uv run --locked python tools/lang.py pack_all_langs --output dist

# Initialize or update a catalog from the source template
uv run --locked python tools/lang.py make_lang --lang de_DE --pot pebbleos.pot
```

Add `--json` to `check_lang` for structured diagnostics. Checks cover catalog
validity, translation progress, font coverage and build limits. Exit status is
0 when build checks pass (warnings allowed), or 1 on errors. Local builds are
for testing and do not enforce publication approval.

The tools can also be installed as a wheel, including their coverage data:

```sh
uv build --wheel
# After installing the wheel in your Python environment:
pebble-lang --root /path/to/catalogs check_lang --lang de_DE
```

## Catalogs and fonts

Each locale folder contains its catalog, `lang_map.json`, fonts and licenses.
The shared `pebbleos.pot` is updated by firmware CI after successful main builds.
Builds need neither a running Weblate service nor a firmware checkout.

Required characters come from the selected language's CLDR baseline and saved
translations, with Arabic presentation forms added where needed. Unknown
baselines produce a warning. No character-list upload is required; legacy lists
can add characters but cannot remove required ones.

Each style compiles the characters missing from its built-in font. Unassigned
styles use the built-in fonts. One `.pbl` works across supported watches, and
identical resources are stored once. `en_IL` provides English strings with Hebrew
font coverage; Hebrew translations use `he_IL`.

Peblate stores uploaded fonts and licenses by content hash inside the locale
folder. Styles can share files, and Git deduplicates identical content across
languages. Compiled fonts and preview caches are generated artifacts.

Coverage gaps are warnings in local checks. Emoji, string-to-style usage and
screen layout are not checked. See [coverage data](data/README.md) for details.

## License

[Apache License 2.0](LICENSE), except where individual notices specify otherwise.
Third-party fonts retain their original licenses; language-baseline data includes
its Unicode license in `data/`.
