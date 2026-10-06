# Built-in font coverage

`builtin_font_coverage.json` records usable codepoints for the base fonts behind
all 20 language-pack slots in normal PebbleOS firmware. It includes the source
commit and SHA-256 hashes of inputs; no font binaries are copied here.

PBF coverage comes from compiled glyph tables, excluding missing-glyph mappings.
For TrueType fonts, coverage uses the font's character map and the firmware's
character-list/regex rules (including its ellipsis exception). PBF files are
copied verbatim by firmware, so resource-map filters do not apply to them.
Board maps are checked for overrides to these slots; the extractor refuses
changes that would invalidate common coverage. Dangling board-map links are
recorded as absent, matching the firmware resource lookup.

This is reference data, not a guarantee of shaping or layout. Numeric and unit
slots intentionally have limited coverage. `check_lang` combines this snapshot
with each built extension and reports remaining alphabet gaps in text styles,
including aliases. The five numeric and unit-only subset styles are marked
`specialized` and excluded from full-alphabet checks. They still compile, but
their actual screen text needs watch testing. Emoji are excluded. String-to-slot
usage and layout are not checked.

For built-in-only packs, publication gates on body and heading (`GOTHIC_*`)
coverage. Other display-style gaps remain warnings because strings are not mapped
to their actual screen styles. Every style must still compile. Custom-font packs
retain explicit maintainer review of licenses, rendering and coverage gaps.

Only maintainers refreshing the snapshot need a PebbleOS Git checkout:

```sh
uv run --locked python tools/extract_builtin_coverage.py \
  --firmware /path/to/pebbleos --revision <commit> \
  --output data/builtin_font_coverage.json
```

Extraction reads committed files, ignoring local modifications. Review firmware
font-generator changes before refreshing. Normal translation builds remain
independent of firmware, and CI only needs to upload the POT.
