# PebbleOS translations

Translation catalogs, language-pack resource maps, fonts, and character sets
for [PebbleOS](https://github.com/coredevices/pebbleos).

## Packing

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and GNU
gettext (`msgfmt`, `msginit`, and `msgmerge` on `PATH`).
On macOS, `brew install gettext` supplies gettext;
on Debian/Ubuntu, install the `gettext` package. Then, from this checkout:

```sh
uv sync --locked
uv run --locked python tools/lang.py pack_lang --lang fr_FR --output dist
uv run --locked python tools/lang.py pack_all_langs --output dist
```

The tools also install as a wheel (`uv build --wheel`), including coverage and
language-character data. Services such as Peblate can install it without mounting
this checkout and run `pebble-lang --root /path/to/catalogs check_lang --lang he`.
The installed CLI defaults to the current directory; the existing script commands
above continue to work.

To initialize or update a language, supply the current source catalog:

```sh
uv run --locked python tools/lang.py make_lang --lang fr_FR --pot /path/to/pebbleos.pot
```

## Validation

Check a language without changing its files or saving a pack:

```sh
uv run --locked python tools/lang.py check_lang --lang fr_FR
uv run --locked python tools/lang.py check_lang --lang fr_FR --json
```

Checks catalogs, translation progress, uploaded-font coverage, and universal
build limits. Use `--json` for structured diagnostics. Exit status is 0 when
build checks pass (warnings allowed), or 1 on errors.

Coverage uses the checked-in base-font snapshot and built font extensions.
Emoji are excluded. Per-slot gaps are warnings: string-to-slot usage and layout
are not checked. Passing checks does not establish publication readiness.

## License

This project is licensed under the [Apache License 2.0](LICENSE), except
where individual files or accompanying notices specify another license.
Third-party fonts retain their original licenses

## Language characters and storage

The selected language supplies an offline [CLDR 48](https://cldr.unicode.org/translation/core-data/exemplars) baseline (main letters, case
variants, and decimal digits). Saved translations add their characters; Arabic
presentation forms are included where needed. Unknown languages produce a
warning rather than an assumed alphabet. `en_IL` remains English with Hebrew font coverage; Hebrew is `he_IL`.

Validation, previews, and pack builds share these requirements. Each font style
compiles only requirements missing from its actual built-in font. Legacy subset
files may add characters but cannot exclude required ones. Unassigned styles
create no font files and fall back to base fonts. One universal `.pbl` contains
translations and needed glyph resources; identical resource bytes are stored once.

Peblate commits source fonts and their required licenses by content hash inside each language folder. Multiple styles reuse the same local file; Git
stores identical file contents across languages as one blob. Maps beside each
catalog reference local filenames. A fresh repository clone can build
packs independently; compiled glyphs and preview caches are generated artifacts.
Language baselines come from `data/language_characters.json`; its Unicode license
is alongside it. No language-specific character-list upload is needed.

The Weblate service and translator UI live in the separate
[Peblate repository](../peblate/README.md). This repository remains usable with uv
without running the service or checking out firmware.
