# ZIP / 7z / tar extraction bench

Closes claim `nzbfast-zip-7z-tar-bench-rows-26sep`. rarkit's format
coverage census (`docs/FORMAT-COVERAGE-26SEP.md`, rarkit branch
`land/format-coverage-26sep`) found that nzbfast ships ZIP, 7z and tar
extraction entirely outside the old RAR engine (nzbkit-base's `zip.rs`
and `tar.rs`, the vendored `sevenz-rust2`/`lzma-rust2` for 7z), backed
by 122 + 8 ZIP tests, 66 + 6 7z tests and 27 tar tests - and that no
nzbfast bench row had ever timed any of them. This directory is that
row.

## Fixtures: `gen-fixtures.py`

Builds three corpora BY RECIPE - never a shared/committed corpus - and
archives each with the box's own `zip`, `7zz` (or `7z`/`7za`) and
`bsdtar`/`tar`:

- `text/` - this repo's own `.rs` source, repeated to size (compressible).
- `binary/` - `os.urandom` (incompressible).
- `many-small/` - thousands of small mixed text/binary files (per-member
  overhead, the shape a single big file cannot show).

Archives per corpus: `.zip` (deflate), `.zip` (store), `.7z` (LZMA2),
`.tar`, `.tar.gz`; `many-small/` additionally gets an explicit
`-ms=on`/`-ms=off` solid/non-solid 7z pair (solid mode only means
anything with more than one member).

```
bench/format-coverage/gen-fixtures.py <out-dir> --size-mib 64 \
    --small-count 4096 --small-total-mib 48
```

## Driver: `run-legs.py`

Runs `nzbfast extract <dir>` (the CLI path - see the doc comment at the
top of the script for why this is the one that reaches the real
readers, and what it does NOT compare) against `7zz x` and `bsdtar -x`
as rivals, over every archive the generator built. House form:
interleaved reps, a same-binary null (`nzbfast` runs twice per leg,
back to back), instructions retired + CPU seconds + peak RSS from
`/usr/bin/time -l` beside every wall figure, byte-for-byte verification
after every extraction, box and load stated in the header line. Wall is
UNTRUSTED unless you pass `--wall` on a box the dispatcher has handed
you as quiet - default is instructions/cpu_s only.

```
bench/format-coverage/run-legs.py <fixtures-dir> --nzbfast <release-bin> \
    --reps 5 --out results.tsv
```

## What `.tar.gz` rows mean

`.tar.gz` is generated because the task asked for it, but it is not a
fair three-way race: nzbfast does not decompress gzip at all (`.tar.gz`
lands on disk as an ordinary file, by design - the census's OUT OF
SCOPE table says so), and 7zz's `x` only unwraps ONE layer of a
`.tar.gz` (it leaves a `.tar` behind, not the final files) where
`bsdtar`/`tar` auto-detects gzip and does both layers in one call. So
every `*-tar-gz` row reports `nzbfast` and `7zz` as `FAILED` by design,
not as a bug in either — `bsdtar` is the only arm that actually
completes the leg. Read it as a coverage finding, not a competitive
loss.

## Which box, and why

Instruction/CPU counts run UNTIMED on one of this fleet's always-loaded
build boxes, coordinated through that box's own coordination file and
`tools/bench-box-gate.py`, per `.claude/skills/bench-suite` item 0. That
box runs at a load average high enough that no wall figure from it is
ever published; a timed round is offered separately, on a box handed
out for that purpose, once this round's counts are in.
