# parfast-strict

This repository is [nzbfast](https://github.com/nzbfast/nzbfast) v1.7.1 with
one change to its PAR2 tool, parfast: a new switch, `--strict-block-size`.
The version line says so; nothing else is different. The switch exists for posting programs that work out
the PAR2 geometry themselves. Such a program needs the PAR2 engine to build the
set at exactly the block size it was given, or to refuse. Upstream parfast
instead adjusts the block size and carries on, which the caller cannot see from
the exit code.

## The switch

`--strict-block-size` acts only when a block size is given with `-s`.

- A block size of 0, or one that is not a multiple of 4, is refused when the
  command line is read. Message: `Block size must be a multiple of 4.`
  (or `Invalid block size option: -s0`). Exit code 3.
- A block size the payload cannot carry within the PAR2 limit of 32,768 input
  slices is refused before any file is written. Message:
  `Block size is too small. It would require <n>blocks.` Exit code 3.

The messages and the exit code are par2cmdline's. Without the switch, behaviour
is identical to upstream: the block size is rounded up to a multiple of 4, or
raised until the payload fits. `-b` is untouched, and so are encoding and
output layout.

## Build

The Rust toolchain is pinned by `rust-toolchain.toml` (1.98.0). A C compiler
is also needed (on Windows, the MSYS2 `mingw-w64-x86_64-gcc` package).

```
cargo build --release -p parfast
```

The binary is `target/release/parfast` (`parfast.exe` on Windows).

## Verify

```
parfast --version
```

Expected: `parfast version 1.7.1 (strict-block-size)`. Upstream's parfast
prints `parfast version 1.7.1`.

Make a file of exactly 131,073 bytes, called `over.bin` here.

```
parfast c --strict-block-size -s4 -c5 out.par2 over.bin
```

Expected: `Block size is too small. It would require 32769blocks.`, exit
code 3, no files written.

```
parfast c --strict-block-size -s105118 -c2 out.par2 over.bin
```

Expected: `Block size must be a multiple of 4.`, exit code 3, no files
written.

The same checks run on every build of this repository:
[`.github/scripts/strict-mode-tests.py`](.github/scripts/strict-mode-tests.py).

## Source, patch and licence

- Upstream: <https://github.com/nzbfast/nzbfast>, tag `parfast-v1.7.1`
  (commit `370da77`).
- The whole change as one patch file:
  [`docs/parfast-strict-block-size.patch`](docs/parfast-strict-block-size.patch).
- Licence: GPL-3.0-or-later, the same as upstream. See [LICENSE](LICENSE) and
  [COPYRIGHT.md](COPYRIGHT.md).
- The source for every released binary is the tagged commit it was built from.
  Release tags are named `parfast-strict-v<upstream version>`.
