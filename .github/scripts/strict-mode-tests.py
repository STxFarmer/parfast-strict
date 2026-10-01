#!/usr/bin/env python3
"""Strict-mode checks for the parfast-strict build.

Usage: strict-mode-tests.py <path to parfast binary>

Four checks, the same on every platform:
  0. --version names this build: "(strict-block-size)".
  1. --strict-block-size refuses a block size that needs 32,769 slices.
  2. --strict-block-size refuses a block size that is not a multiple of 4.
  3. Without the switch the behaviour is upstream's: the block size is
     raised from 4 to 8 and the set is written.
Exits non-zero on the first mismatch.
"""
import glob
import os
import struct
import subprocess
import sys
import tempfile

MAIN_TYPE = b"PAR 2.0\x00Main\x00\x00\x00\x00"
MAGIC = b"PAR2\x00PKT"


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def run(parfast, args, cwd):
    p = subprocess.run([parfast] + args, cwd=cwd, capture_output=True, text=True)
    print("$ parfast " + " ".join(args))
    print("  exit code: %d" % p.returncode)
    for line in (p.stdout + p.stderr).splitlines()[:6]:
        print("  | " + line)
    return p


def main_slice_size(path):
    """Slice size recorded in the first Main packet of a PAR2 file."""
    data = open(path, "rb").read()
    pos = data.find(MAGIC)
    while pos != -1:
        length = struct.unpack_from("<Q", data, pos + 8)[0]
        if data[pos + 48:pos + 64] == MAIN_TYPE:
            return struct.unpack_from("<Q", data, pos + 64)[0]
        pos = data.find(MAGIC, pos + max(length, 8))
    return None


def expect_refusal(parfast, cwd, args, stem, message):
    p = run(parfast, args, cwd)
    if p.returncode != 3:
        fail("expected exit code 3, got %d" % p.returncode)
    if message not in p.stderr:
        fail("expected %r on stderr" % message)
    written = glob.glob(os.path.join(cwd, stem + "*.par2"))
    if written:
        fail("files were written: %s" % ", ".join(map(os.path.basename, written)))
    print("  PASS")


def main():
    if len(sys.argv) != 2:
        fail("usage: strict-mode-tests.py <parfast binary>")
    parfast = os.path.abspath(sys.argv[1])
    cwd = tempfile.mkdtemp(prefix="parfast-strict-")
    with open(os.path.join(cwd, "over.bin"), "wb") as f:
        f.write(b"\0" * 131073)
    with open(os.path.join(cwd, "f.bin"), "wb") as f:
        f.write(b"\0" * 1000000)

    # 0. the version line names this build
    p = run(parfast, ["--version"], cwd)
    if p.returncode != 0 or "(strict-block-size)" not in p.stdout:
        fail("expected '(strict-block-size)' in the --version line")
    print("  PASS")

    # 1. 32,769-slice refusal
    expect_refusal(
        parfast, cwd,
        ["c", "--strict-block-size", "-s4", "-c5", "out.par2", "over.bin"],
        "out", "Block size is too small. It would require 32769blocks.",
    )
    # 2. multiple-of-4 refusal
    expect_refusal(
        parfast, cwd,
        ["c", "--strict-block-size", "-s105118", "-c2", "out.par2", "f.bin"],
        "out", "Block size must be a multiple of 4.",
    )
    # 3. unchanged without the switch
    p = run(parfast, ["c", "-s4", "-c5", "out2.par2", "over.bin"], cwd)
    if p.returncode != 0:
        fail("expected exit code 0, got %d" % p.returncode)
    index = os.path.join(cwd, "out2.par2")
    if not os.path.exists(index):
        fail("out2.par2 was not written")
    size = main_slice_size(index)
    if size != 8:
        fail("expected Main packet slice size 8, got %r" % size)
    print("  PASS (Main packet slice size 8)")
    print("All strict-mode tests passed.")


if __name__ == "__main__":
    main()
