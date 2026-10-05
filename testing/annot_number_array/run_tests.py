#!/usr/bin/env python3
"""Scripted test for FPDFAnnot_GetNumberArray (annot_number_array.patch).

usage: run_tests.py PDFIUM_DIR [--work DIR]
PDFIUM_DIR = an unpacked pdfium-binaries artifact (include/, lib/); macOS or
Linux (cc). Generates the fixture (stdlib only), builds driver.c against the
artifact and checks every case. Every case must run; exit 0 = all passed.
"""
import json, os, platform, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")


def classic(objs, root=1):
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offs = {}
    for n in sorted(objs):
        offs[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    size = max(objs) + 1
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f\r\n" % size
    for n in range(1, size):
        out += (b"%010d 00000 n\r\n" % offs[n]) if n in offs else b"0000000000 00000 f\r\n"
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, root, x)
    return bytes(out)


SQ = b"/Type /Annot /Subtype /Square /Rect [100 100 150 150] /F 4 "
FIXTURE = {
    1: b"<< /Type /Catalog /Pages 2 0 R >>",
    2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> "
       b"/Annots [10 0 R 11 0 R 12 0 R 13 0 R 14 0 R] >>",
    10: b"<< " + SQ + b"/C [1 0 0] /AP << /N 20 0 R >> >>",   # /AP, no /IC
    11: b"<< " + SQ + b"/C [0 0 1] >>",                       # no /AP
    12: b"<< " + SQ + b"/C 15 0 R /IC [0.75 17 0 R 0] >>",    # indirect array/element
    13: b"<< " + SQ + b"/C [1 (x) 0] >>",                     # mixed types
    14: b"<< " + SQ + b"/C [] >>",                            # empty
    15: b"[0.5 0.25 0.125]",
    17: b"0.5",
    20: b"<< /Type /XObject /Subtype /Form /BBox [100 100 150 150] /Length 8 >>\nstream\n0 0 m S\n\nendstream",
}

S = -99.0  # sentinel: not written
EXPECTED = {
    "c_with_ap": (3, [1, 0, 0, S]),
    "c_no_ap": (3, [0, 0, 1, S]),
    "ic_absent": (-1, [S, S, S, S]),
    "not_an_array": (-1, [S, S, S, S]),
    "indirect_array": (3, [0.5, 0.25, 0.125, S]),
    "indirect_element": (3, [0.75, 0.5, 0, S]),
    "mixed_types": (-1, [S, S, S, S]),
    "empty_array": (0, [S, S, S, S]),
    "size_query_null": (3, [S, S, S, S]),
    "size_query_count0": (3, [S, S, S, S]),
    "truncated_copy": (3, [1, 0, S, S]),
    "null_key": (-1, [S, S, S, S]),
}


def main():
    os.makedirs(WORK, exist_ok=True)
    fixture = os.path.join(WORK, "number_arrays.pdf")
    open(fixture, "wb").write(classic(FIXTURE))
    lib = os.path.join(WORK, "lib")
    os.makedirs(lib, exist_ok=True)
    name = "libpdfium.dylib" if platform.system() == "Darwin" else "libpdfium.so"
    shutil.copy(os.path.join(PD, "lib", name), lib)
    if platform.system() == "Darwin":
        subprocess.run(["install_name_tool", "-id", "@rpath/" + name, os.path.join(lib, name)],
                       check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["codesign", "-f", "-s", "-", os.path.join(lib, name)], stderr=subprocess.DEVNULL)
    exe = os.path.join(WORK, "driver")
    subprocess.run(["cc", "-O1", "-I", os.path.join(PD, "include"), os.path.join(HERE, "driver.c"),
                    "-L", lib, "-lpdfium", "-Wl,-rpath," + lib, "-o", exe], check=True)
    r = subprocess.run([exe, fixture], capture_output=True, text=True)
    results = {}
    for line in r.stdout.splitlines():
        d = json.loads(line)
        results[d["case"]] = d
    failures = []

    def report(case, msgs):
        print("%-22s %s" % (case, "PASS" if not msgs else "FAIL: " + "; ".join(msgs)))
        if msgs:
            failures.append(case)

    for case, (ret, values) in EXPECTED.items():
        d = results.get(case)
        if d is None:
            report(case, ["did not run (driver rc=%d)" % r.returncode])
            continue
        msgs = []
        if d["ret"] != ret:
            msgs.append("returned %s, expected %s" % (d["ret"], ret))
        if any(abs(a - b) > 1e-6 for a, b in zip(d["values"], values)):
            msgs.append("values %s, expected %s" % (d["values"], values))
        report(case, msgs)
    d = results.get("getcolor_with_ap")
    report("getcolor_with_ap", [] if d and d["ok"] == 0 else
           ["expected stock FPDFAnnot_GetColor to fail with /AP (context for the new API)"])
    d = results.get("nomutate")
    report("nomutate", [] if d and d["equal"] == 1 else
           ["the document's object map or objects changed"])
    print("\n%d case(s), %d failure(s)" % (len(EXPECTED) + 2, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
