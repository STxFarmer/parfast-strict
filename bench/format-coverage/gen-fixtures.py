#!/usr/bin/env python3
"""Build the ZIP / 7z / tar bench fixtures, by RECIPE, never a shared corpus.

WHY BY RECIPE. Every corpus here is regenerated from a seeded PRNG plus
this repo's own source text, never copied from a shared/committed blob -
the same discipline `tools/rarbench.py` documents at its own `gen_text`
and `gen_small_tree`. Nothing under this directory ships a binary
fixture; running this script is how you get one, in your own scratch
directory.

WHAT IT BUILDS. Three corpora, then every archive shape the census in
`docs/FORMAT-COVERAGE-26SEP.md` (rarkit, branch `land/format-coverage-26sep`)
found untimed for nzbfast's own ZIP/7z/tar readers:

  text/     compressible payload - this repo's own `.rs` source, repeated
            and rotated to size (the `gen_text` recipe).
  binary/   incompressible payload - `os.urandom`, unseedable by design
            (a CSPRNG has no seed argument on this Python; the point is
            "does not fold under LZMA/deflate", not "is reproducible
            byte for byte" - see NOTE below).
  many-small/  a tree of thousands of small mixed text/binary files, the
            shape that prices per-member overhead rather than throughput
            (the `gen_small_tree` recipe): many small files means "the
            work is many small parses, not one big one" - the metric a
            single-file corpus cannot show.

Each corpus is archived with the BOX'S OWN `zip`, `7zz` (or `7z`/`7za`)
and `bsdtar`/`tar`:

  text.zip.deflate   zip -6            (default deflate)
  text.zip.store     zip -0            (stored, no compression)
  text.7z            7zz -mx=5 (LZMA2, the format's default codec)
  text.tar           tar -cf           (ustar, uncompressed)
  text.tar.gz        tar -czf          (gzip -6 over ustar)

...and the same five for binary/. many-small/ additionally gets a SOLID
and a NON-SOLID 7z (`-ms=on` / `-ms=off`) - solid mode only means
anything with more than one member, so the two single-file corpora do
not carry that split; this is the fixture that answers "does packing
many small members into one solid stream change extraction cost".

NOTE on reproducibility. `os.urandom` cannot be seeded, so `binary/`'s
bytes differ between runs. That is fine for a bench fixture (every arm
in a round reads the SAME on-disk bytes; only re-running this generator
changes them) and is called out here rather than left to be discovered.
`text/` and `many-small/`'s text half ARE reproducible (a fixed seed,
sorted-input source text) - only `many-small/`'s per-file binary half
inherits the same unseedable urandom limitation.

USAGE

    bench/format-coverage/gen-fixtures.py <out-dir> [--size-mib N]
        [--small-count N] [--small-total-mib N] [--tools zip=...,7z=...,tar=...]

    --size-mib        text/ and binary/ payload size, MiB (default 64)
    --small-count      file count in many-small/ (default 4096)
    --small-total-mib  total bytes in many-small/, MiB (default 48)
    --list            print the planned archive matrix and exit, no I/O

Regenerates only when the corpus directory is absent; delete a corpus
subdirectory to force a rebuild of it (and everything archived from it -
the script does not diff old archives against new source).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def find_tool(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def gen_text(path: Path, size: int, roots: list[Path]) -> None:
    """Compressible payload: this repo's own `.rs` source, concatenated
    and repeated to size. Falls back to a seeded word salad if no source
    tree is reachable (a stripped export, say)."""
    parts: list[bytes] = []
    for root in roots:
        for p in sorted(root.rglob("*.rs")):
            # Skip vendor/ - out of scope for this lane, and it is by
            # far the largest .rs tree here, which would make the text
            # corpus mostly vendored C++-adjacent glue rather than our
            # own code.
            if "vendor" in p.parts:
                continue
            try:
                parts.append(p.read_bytes())
            except OSError:
                pass
    blob = b"\n".join(parts)
    if not blob:
        rng = random.Random(7)
        words = [f"w{rng.randrange(5000)}" for _ in range(200000)]
        blob = " ".join(words).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        written = 0
        while written < size:
            take = min(len(blob), size - written)
            out.write(blob[:take])
            written += take


def gen_binary(path: Path, size: int) -> None:
    """Incompressible payload: os.urandom, streamed in chunks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        left = size
        while left > 0:
            chunk = os.urandom(min(1 << 20, left))
            out.write(chunk)
            left -= len(chunk)


def gen_small_tree(root: Path, total: int, count: int, text_pool: bytes) -> None:
    """Many small mixed text/binary files across a handful of
    subdirectories, sizes from a seeded PRNG - the `gen_small_tree`
    recipe `tools/rarbench.py` uses for the same reason: per-member
    overhead is invisible in a single-big-file corpus."""
    rng = random.Random(20260926)
    avg = max(256, total // count)
    if len(text_pool) < 64 * 1024:
        text_pool = (text_pool or b"lorem ipsum ") * (64 * 1024 // max(1, len(text_pool)) + 1)
    written = 0
    for i in range(count):
        sub = root / f"d{i % 24:02d}"
        sub.mkdir(parents=True, exist_ok=True)
        size = max(16, rng.randrange(max(17, avg // 4), max(18, avg * 7 // 4)))
        p = sub / f"f{i:05d}"
        if i % 3 == 0:
            # binary third
            p.write_bytes(os.urandom(size))
        else:
            off = rng.randrange(0, max(1, len(text_pool) - 1))
            buf = (text_pool[off:] + text_pool[:off])
            while len(buf) < size:
                buf += buf
            p.write_bytes(buf[:size])
        written += size
    return written


def sha256_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def archive_corpus(
    label: str,
    src: Path,
    out_dir: Path,
    zip_bin: str,
    sevenz_bin: str,
    tar_bin: str,
    solid_variant: bool,
) -> list[tuple[str, Path]]:
    """Build the standard five archives (deflate zip, store zip, 7z
    LZMA2, tar, tar.gz) over `src` (a single file or a directory), plus
    a solid/non-solid 7z pair when `solid_variant` is set (many-small
    only). Returns [(archive-tag, path), ...]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, Path]] = []
    is_dir = src.is_dir()
    members = [str(p.relative_to(src)) for p in sorted(src.rglob("*"))] if is_dir else [src.name]
    workdir = src if is_dir else src.parent

    def m(*names: str) -> list[str]:
        return list(names) if not is_dir else members

    zip_deflate = out_dir / f"{label}.zip"
    run([zip_bin, "-q", "-6", "-r", str(zip_deflate.resolve())] + m(src.name), cwd=workdir)
    made.append((f"{label}-zip-deflate", zip_deflate))

    zip_store = out_dir / f"{label}-store.zip"
    run([zip_bin, "-q", "-0", "-r", str(zip_store.resolve())] + m(src.name), cwd=workdir)
    made.append((f"{label}-zip-store", zip_store))

    if not solid_variant:
        # 7-Zip's own default for a directory with more than one member
        # is solid (-ms=on), so a plain `-mx=5` run over many-small/
        # would just be a second copy of the -ms=on arm below - skip it
        # here and let the solid/non-solid pair speak for many-small.
        sevenz = out_dir / f"{label}.7z"
        run([sevenz_bin, "a", "-bso0", "-bsp0", "-mx=5", str(sevenz.resolve())] + m(src.name), cwd=workdir)
        made.append((f"{label}-7z-lzma2", sevenz))

    tarf = out_dir / f"{label}.tar"
    env = dict(os.environ, COPYFILE_DISABLE="1")
    subprocess.run(
        [tar_bin, "-cf", str(tarf.resolve())] + m(src.name),
        cwd=workdir, check=True, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    made.append((f"{label}-tar", tarf))

    targz = out_dir / f"{label}.tar.gz"
    subprocess.run(
        [tar_bin, "-czf", str(targz.resolve())] + m(src.name),
        cwd=workdir, check=True, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    made.append((f"{label}-tar-gz", targz))

    if solid_variant:
        solid = out_dir / f"{label}-solid.7z"
        run([sevenz_bin, "a", "-bso0", "-bsp0", "-mx=5", "-ms=on", str(solid.resolve())] + m(src.name), cwd=workdir)
        made.append((f"{label}-7z-solid", solid))

        nonsolid = out_dir / f"{label}-nonsolid.7z"
        run([sevenz_bin, "a", "-bso0", "-bsp0", "-mx=5", "-ms=off", str(nonsolid.resolve())] + m(src.name), cwd=workdir)
        made.append((f"{label}-7z-nonsolid", nonsolid))

    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--size-mib", type=int, default=64)
    ap.add_argument("--small-count", type=int, default=4096)
    ap.add_argument("--small-total-mib", type=int, default=48)
    ap.add_argument("--zip-bin", default=find_tool("zip"))
    ap.add_argument("--sevenz-bin", default=find_tool("7zz", "7z", "7za"))
    ap.add_argument("--tar-bin", default=find_tool("bsdtar", "tar"))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("corpora: text/ binary/ many-small/")
        print("archives per corpus: zip-deflate zip-store 7z-lzma2 tar tar-gz")
        print("many-small/ also gets: 7z-solid 7z-nonsolid")
        return 0

    if not args.zip_bin:
        sys.exit("no `zip` binary found on PATH")
    if not args.sevenz_bin:
        sys.exit("no 7-Zip CLI found (7zz/7z/7za) on PATH")
    if not args.tar_bin:
        sys.exit("no `bsdtar`/`tar` binary found on PATH")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    size = args.size_mib * 1024 * 1024
    small_total = args.small_total_mib * 1024 * 1024

    corpus_root = out / "corpus"
    archive_root = out / "archives"
    manifest_lines: list[str] = []

    # --- text/ ---
    text_dir = corpus_root / "text"
    if not text_dir.exists():
        gen_text(text_dir / "text.dat", size, [REPO_ROOT / "crates"])
    made = archive_corpus("text", text_dir / "text.dat", archive_root / "text", args.zip_bin, args.sevenz_bin, args.tar_bin, solid_variant=False)
    for tag, p in made:
        manifest_lines.append(f"{tag}\t{p}\t{p.stat().st_size}")

    # --- binary/ ---
    bin_dir = corpus_root / "binary"
    if not bin_dir.exists():
        gen_binary(bin_dir / "binary.dat", size)
    made = archive_corpus("binary", bin_dir / "binary.dat", archive_root / "binary", args.zip_bin, args.sevenz_bin, args.tar_bin, solid_variant=False)
    for tag, p in made:
        manifest_lines.append(f"{tag}\t{p}\t{p.stat().st_size}")

    # --- many-small/ ---
    small_dir = corpus_root / "many-small"
    if not small_dir.exists():
        text_pool = b""
        for p in sorted((REPO_ROOT / "tools").glob("*.py"))[:20]:
            try:
                text_pool += p.read_bytes()
            except OSError:
                pass
        small_dir.mkdir(parents=True, exist_ok=True)
        gen_small_tree(small_dir, small_total, args.small_count, text_pool)
    made = archive_corpus("many-small", small_dir, archive_root / "many-small", args.zip_bin, args.sevenz_bin, args.tar_bin, solid_variant=True)
    for tag, p in made:
        manifest_lines.append(f"{tag}\t{p}\t{p.stat().st_size}")

    manifest = out / "MANIFEST.tsv"
    manifest.write_text("\n".join(manifest_lines) + "\n")
    print(f"wrote {len(manifest_lines)} archives, manifest at {manifest}")
    for corpus, d in (("text", text_dir), ("binary", bin_dir), ("many-small", small_dir)):
        print(f"  {corpus}: content sha256(tree)={sha256_tree(d)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
