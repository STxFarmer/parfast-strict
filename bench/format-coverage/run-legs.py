#!/usr/bin/env python3
"""Drive nzbfast's own ZIP/7z/tar extraction against 7zz and bsdtar, over
the fixtures `gen-fixtures.py` builds. House form: interleaved reps,
paired median, a same-binary null arm, CPU seconds and peak RSS beside
every wall, box and load stated.

THE CLI PATH THIS TIMES. `nzbfast extract <dir>` - the doc comment on
`Command::Extract` in `crates/nzbfast/src/lib.rs` calls it "the same
repair+extract pipeline the daemon runs after a download, pointed at
local files". It dispatches through `extract_local` ->
`extract_nested_why` -> `extract_nested_capped`
(`crates/nzbfast-unpack/src/unpack.rs`), which is the IDENTICAL ladder
`crates/nzbfast-engine/src/get/tail.rs` calls after a real download -
the CLI and the daemon reach the same `extract_zip`/`extract_sevenz`/
`extract_tar` functions in `crates/nzbfast-unpack/src/rarfix.rs` and
`rarfix/sevenz.rs`. There is no `nzbfast unpack` subcommand and no
"point it at one file" switch - the command takes a DIRECTORY holding
the assembled archive (+ optional .par2 set), which is why every arm
here gets its own directory holding exactly one archive.

WHAT THIS IS NOT. `7zz x` and `bsdtar -x` never speak NNTP - they read
a local file. So this measures nzbfast's OWN extraction cost (no
network, no PAR2 set present, no download involved) against the rivals'
extraction cost, apples to apples: all three read the archive already
sitting on local disk and write files to a local directory. Nothing
here claims anything about download-time cost.

MEASUREMENT. `/usr/bin/time -l` around each child (macOS only - this
whole fleet's bench boxes are Macs, see `.claude/skills/bench-suite`):
wall (real), cpu_s (user+sys),
peak_rss (peak memory footprint, bytes), and instructions retired.
Every archive is verified byte-for-byte against the corpus tree after
extraction; a mismatch is a FAILED cell, not a number.

THE SAME-BINARY NULL. Every leg runs the `nzbfast` arm TWICE
(`nzbfast-a`, `nzbfast-b`), identical binary and args, back to back in
the same interleaved order as the real rivals - so the null's own
spread is printed beside the competitive numbers, and a wall/cpu
difference smaller than the null's own a/b spread is noise, not a
finding (the convention `research/codex-briefs-2026-09-26/CODEX-NOTE-03-*`
calls "instructions retired with a same-binary null beside every row").

ARMS ARE INTERLEAVED per rep (the starting arm rotates each rep) so a
box whose load moves during the round moves every arm together, per
`tools/rarbench.py`'s own convention.

USAGE

    bench/format-coverage/run-legs.py <fixtures-dir> --nzbfast <bin>
        [--reps 5] [--legs text-zip-deflate,...] [--out results.tsv]
        [--wall | --no-wall]

    --wall / --no-wall   whether to trust the wall-clock column at all
                          (default: --no-wall, i.e. UNTIMED - print
                          instructions/cpu_s only and mark wall
                          indicative). Pass --wall only on a box the
                          dispatcher has handed you as quiet.

Prints one LEG line per (archive, arm, rep) to stdout and appends to
--out if given.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TIME_BIN = "/usr/bin/time"


@dataclass
class Sample:
    wall: float
    cpu: float
    rss: int | None
    ins: int | None
    rc: int
    stderr_tail: str


def parse_time_l(stderr: str) -> tuple[float | None, int | None, int | None]:
    """(cpu_s, peak_rss_bytes, instructions_retired) from `time -l`'s
    stderr block. cpu_s is user+sys off the ` real`/`user`/`sys` line."""
    user = sys_ = None
    rss = ins = None
    for line in stderr.splitlines():
        s = line.strip()
        low = s.lower()
        if "real" in s and "user" in s and s.endswith("sys"):
            # "  0.20 real         0.01 user         0.00 sys"
            parts = s.split()
            try:
                real_i = parts.index("real")
                user_i = parts.index("user")
                sys_i = parts.index("sys")
                user = float(parts[user_i - 1])
                sys_ = float(parts[sys_i - 1])
            except (ValueError, IndexError):
                pass
        elif "maximum resident set size" in low or "peak memory footprint" in low:
            digits = "".join(ch for ch in s if ch.isdigit())
            if digits:
                rss = max(rss or 0, int(digits))
        elif "instructions retired" in low:
            digits = "".join(ch for ch in s if ch.isdigit())
            if digits:
                ins = int(digits)
    cpu = (user + sys_) if (user is not None and sys_ is not None) else None
    return cpu, rss, ins


def run_timed(cmd: list[str], cwd: Path | None = None) -> Sample:
    wrapper = [TIME_BIN, "-l"] if platform.system() == "Darwin" else [TIME_BIN, "-v"]
    if not Path(TIME_BIN).is_file():
        wrapper = []
    t0 = time.perf_counter()
    proc = subprocess.run(
        wrapper + cmd, cwd=cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    wall = time.perf_counter() - t0
    cpu, rss, ins = parse_time_l(proc.stderr) if wrapper else (None, None, None)
    tail = "\n".join(proc.stderr.splitlines()[-3:]) if proc.returncode != 0 else ""
    return Sample(wall=wall, cpu=cpu or 0.0, rss=rss, ins=ins, rc=proc.returncode, stderr_tail=tail)


def sha256_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def corpus_dir_for(archive_tag: str, fixtures: Path) -> Path:
    """archive_tag like 'text-zip-deflate' -> fixtures/corpus/text (the
    reference tree to verify extraction against)."""
    for name in ("text", "binary", "many-small"):
        if archive_tag.startswith(name):
            return fixtures / "corpus" / name
    sys.exit(f"cannot map archive tag {archive_tag!r} to a corpus dir")


def load_manifest(fixtures: Path) -> dict[str, Path]:
    manifest = fixtures / "MANIFEST.tsv"
    out: dict[str, Path] = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        tag, path, _size = line.split("\t")
        out[tag] = Path(path)
    return out


def extract_nzbfast(nzbfast_bin: Path, archive: Path, out_root: Path) -> Sample:
    d = out_root / "nzbfast"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    shutil.copy2(archive, d / archive.name)
    return run_timed([str(nzbfast_bin), "extract", str(d)])


def extract_7zz(sevenz_bin: str, archive: Path, out_root: Path) -> Sample:
    d = out_root / "7zz"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return run_timed([sevenz_bin, "x", "-bso0", "-bsp0", "-y", f"-o{d}", str(archive)])


def extract_bsdtar(tar_bin: str, archive: Path, out_root: Path) -> Sample:
    d = out_root / "bsdtar"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return run_timed([tar_bin, "-xf", str(archive), "-C", str(d)])


def verify(extracted_dir: Path, corpus_dir: Path, archive: Path) -> str | None:
    """None on match; else a short mismatch description. Compares file
    content only (not the archive itself, which the extractor may or
    may not have left behind)."""
    corpus_files = {p.relative_to(corpus_dir) for p in corpus_dir.rglob("*") if p.is_file()}
    for rel in corpus_files:
        got = extracted_dir / rel
        want = corpus_dir / rel
        if not got.is_file():
            # nzbfast's own dir keeps the archive itself alongside the
            # extracted payload; look one level down too (some tools
            # nest under the corpus dir name).
            alt = extracted_dir / archive.stem / rel
            if alt.is_file():
                got = alt
            else:
                return f"missing {rel}"
        if got.read_bytes() != want.read_bytes():
            return f"content differs: {rel}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixtures_dir", type=Path)
    ap.add_argument("--nzbfast", type=Path, required=True)
    ap.add_argument("--sevenz-bin", default=shutil.which("7zz") or shutil.which("7z") or shutil.which("7za"))
    ap.add_argument("--tar-bin", default=shutil.which("bsdtar") or shutil.which("tar"))
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--legs", default="")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--wall", dest="wall", action="store_true")
    ap.add_argument("--no-wall", dest="wall", action="store_false")
    ap.set_defaults(wall=False)
    ap.add_argument("--scratch", type=Path)
    args = ap.parse_args()

    if not args.nzbfast.is_file():
        sys.exit(f"nzbfast binary not found: {args.nzbfast}")
    if not args.sevenz_bin:
        sys.exit("no 7-Zip CLI found (7zz/7z/7za)")
    if not args.tar_bin:
        sys.exit("no bsdtar/tar binary found")

    manifest = load_manifest(args.fixtures_dir)
    wanted = set(args.legs.split(",")) if args.legs else set(manifest)
    legs = [t for t in manifest if t in wanted]
    if not legs:
        sys.exit("no legs selected")

    scratch = args.scratch or (args.fixtures_dir / "run-scratch")
    scratch.mkdir(parents=True, exist_ok=True)

    out_fh = open(args.out, "a") if args.out else None
    load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else float("nan")
    box = platform.node()

    def emit(line: str) -> None:
        print(line)
        if out_fh:
            out_fh.write(line + "\n")
            out_fh.flush()

    emit(f"# box={box} load1={load1:.1f} wall_trusted={args.wall} reps={args.reps} "
         f"nzbfast={args.nzbfast} sevenz={args.sevenz_bin} tar={args.tar_bin}")

    for tag in legs:
        archive = manifest[tag]
        corpus_dir = corpus_dir_for(tag, args.fixtures_dir)
        arms = ["nzbfast-a", "nzbfast-b", "7zz", "bsdtar"]
        for rep in range(1, args.reps + 1):
            order = arms[rep % len(arms):] + arms[: rep % len(arms)]
            for arm in order:
                d = scratch / tag
                d.mkdir(parents=True, exist_ok=True)
                if arm in ("nzbfast-a", "nzbfast-b"):
                    s = extract_nzbfast(args.nzbfast, archive, d)
                    extracted = d / "nzbfast"
                elif arm == "7zz":
                    s = extract_7zz(args.sevenz_bin, archive, d)
                    extracted = d / "7zz"
                else:
                    s = extract_bsdtar(args.tar_bin, archive, d)
                    extracted = d / "bsdtar"

                mismatch = None
                if s.rc == 0:
                    mismatch = verify(extracted, corpus_dir, archive)
                status = "ok" if (s.rc == 0 and mismatch is None) else "FAILED"
                extra = ""
                if s.rc != 0:
                    extra = f" err={s.stderr_tail.replace(chr(10), '|')[:120]!r}"
                elif mismatch:
                    extra = f" mismatch={mismatch!r}"

                wall_field = f"{s.wall:.3f}" if args.wall else "untrusted"
                ins_field = s.ins if s.ins is not None else "na"
                rss_mb = f"{s.rss / (1024*1024):.1f}" if s.rss else "na"
                emit(
                    f"LEG {tag} {arm} rep={rep} status={status} "
                    f"wall_s={wall_field} cpu_s={s.cpu:.3f} rss_mb={rss_mb} "
                    f"ins={ins_field} rc={s.rc}{extra}"
                )
                shutil.rmtree(extracted, ignore_errors=True)

    if out_fh:
        out_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
