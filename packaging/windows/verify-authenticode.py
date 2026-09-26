#!/usr/bin/env python3
"""Read the Authenticode signature out of a Windows PE, on a Mac.

    packaging/windows/verify-authenticode.py <file.exe> [...]

Prints the PE security directory, the WIN_CERTIFICATE header and writes
the PKCS#7 blob beside each input as `<file>.p7b`, which
`openssl pkcs7 -inform DER -in <file>.p7b -print_certs -noout` then
reads for the subject and issuer.

WHY THIS EXISTS. `signtool` is Windows-only and `osslsigncode` is not
installed on the release Mac, so after a SignPath run there was no way
here to answer "is this file actually signed, and by which certificate?"
other than trusting a green CI step. That is not good enough: on 21 Sep
2026 the signing step went green against an artifact configuration that
could not have signed anything, and the first real failure was only
visible in the SignPath console. `strings` does not answer it either -
the certificate is DER inside the security directory, so the subject CN
does not show up as a plain string.

Used to verify run 35635369454: both installers carried a PKCS#7
structure (revision 0x0200, type 0x0002) with
`subject=CN=Test certificate for 'nzbfast [OSS]'`.

Not a gate. A gate would have to know which certificate is expected,
and that changes when the release certificate replaces the test one.
"""

import struct, sys, pathlib

def security_dir(p):
    d = pathlib.Path(p).read_bytes()
    e_lfanew = struct.unpack_from('<I', d, 0x3C)[0]
    assert d[e_lfanew:e_lfanew+4] == b'PE\0\0', 'not a PE'
    coff = e_lfanew + 4
    opt = coff + 20
    magic = struct.unpack_from('<H', d, opt)[0]
    # data directories start at 96 (PE32) or 112 (PE32+)
    dd = opt + (96 if magic == 0x10b else 112)
    # entry 4 = IMAGE_DIRECTORY_ENTRY_SECURITY
    rva, size = struct.unpack_from('<II', d, dd + 4*8)
    return d, rva, size

for f in sys.argv[1:]:
    d, off, size = security_dir(f)
    print(f"\n=== {f}")
    if size == 0:
        print("  NO SIGNATURE (security directory empty)")
        continue
    print(f"  security directory: offset={off} size={size}")
    # WIN_CERTIFICATE header: dwLength(4) wRevision(2) wCertificateType(2)
    length, rev, ctype = struct.unpack_from('<IHH', d, off)
    print(f"  WIN_CERTIFICATE len={length} revision=0x{rev:04x} type=0x{ctype:04x} (0x0002 = PKCS#7)")
    pkcs7 = d[off+8:off+length]
    pathlib.Path(f + '.p7b').write_bytes(pkcs7)
