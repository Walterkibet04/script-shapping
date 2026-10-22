Write an OpenType feature file at `/app/features.fea` that gives a joining script with vowel marks its correct layout.

**Situation**

The typeface is for an undocumented script. Every glyph is drawn, named and mapped in `cmap`, but the typeface has no GSUB or GPOS tables, so text sets as isolated default letterforms with nothing joined and every vowel mark at the origin. Glyph names are meaningless by design, and the outlines do not reveal what a glyph is for. The rules of the script have to be inferred from worked examples.

**Inputs (all in `/app`)**

- `/app/font.ttf`: the font with its layout tables removed. Outlines, `cmap` and advance widths are final.
- `/app/anchors.json`: for every glyph that can carry a mark, a `top` and a `bottom` anchor; for every mark glyph that attaches, one anchor and which of `top` or `bottom` it attaches to.
- `/app/classes.json`: the glyphs in the `marks` list (used by the engine's mark handling, described below).
- `/app/proofs.json`: 40 proof lines. Each has `input`, a list of codepoints, and `output`, the exact result of the finished font: one `[glyph name, x offset, y offset, x advance]` entry per output glyph, in whole font units.
- `/app/engine/shaper.py`: the shaping engine used to produce the proofs and to grade.

The script's behaviour is the kind expected of a joining script with vowel marks. The glyph set already contains every glyph the rules need.

**Tooling**

`/app/features.fea` is compiled into `/app/font.ttf` with fontTools feaLib (4.62.1) and then shaped by `/app/engine/shaper.py`.

- Compile and compare against all 40 proof lines: `python3 /app/engine/shaper.py check /app/features.fea`
- Shape one line: `python3 /app/engine/shaper.py shape /app/features.fea U+E001 U+E010`

**Engine behaviour**

The engine is much smaller than a production shaper. `shaper.py` is the authoritative source; in summary:

- Supported lookups: GSUB single, multiple, ligature and chaining contextual (any subtable format); GPOS single, pair (glyph or class) and mark-to-base. Any other lookup type is an error, as is any lookup flag other than `0` or `IgnoreMarks`.
- Every lookup referenced by any feature runs, regardless of feature tag or script, in lookup index order, all GSUB lookups before all GPOS lookups. A lookup referenced only from inside a contextual rule does not run on its own.
- Each lookup walks the glyphs left to right and tries its subtables in order at each position; the first matching rule is applied.
- `IgnoreMarks` skips the glyphs listed under `marks` in `/app/classes.json`. This applies to rule context, to the gaps between ligature components, and to the glyphs in front of the second glyph of a pair. Mark-to-base attachment uses the same list: it steps back over those glyphs to the first glyph that is not one.
- A contextual rule may call a multiple or ligature lookup only as the single lookup it calls, and a contextual lookup may not call another contextual lookup.
- A multiple substitution to `NULL` deletes the glyph.
- With `IgnoreMarks` on a ligature lookup, marks skipped between components remain in the line, after the ligature glyph.
- A pair rule adds its first value to the first glyph and its second value to the second.
- Mark-to-base attachment sets the mark's offset to the base anchor minus the mark anchor, replacing any earlier offset.
- After all lookups have run, each attached mark gets its base's final offset added to its own, and its x offset is reduced by the advances of everything from the base up to the mark. The reported figures are these final values.

**Grading**

`/app/features.fea` is compiled into a sealed copy of the font and used to shape 12 further proof lines that are not provided. All 12 must match exactly, including glyph names and every integer. Every rule the 12 lines rely on is demonstrated somewhere in the 40, though not necessarily on the same glyph combinations, and matching all 40 does not by itself guarantee the rules are right. Lookup structure, ordering and feature tags are up to you provided the text sets correctly.

You have 9000 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
