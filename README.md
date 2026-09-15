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
