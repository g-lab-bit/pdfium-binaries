#!/usr/bin/env python3
"""Scripted test for fork_p17.patch: an in-place (incremental) save is not
refused because a loaded object holds a dangling reference.

usage: run_tests.py PDFIUM_DIR [--work DIR] [--expect-p16]
Builds driver.c against the artifact. Exit status 0 = all passed.
--expect-p16: the unpatched behaviour (the walk case refuses) is expected —
proves the test catches the bug on a pre-p17 artifact.
"""
import os, platform, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
EXPECT_P16 = "--expect-p16" in sys.argv
os.makedirs(WORK, exist_ok=True)


def classic(objs):
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offs = {}
    for n in sorted(objs):
        offs[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    size = max(objs) + 1
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f\r\n" % size
    for n in range(1, size):
        out += (b"%010d 00000 n\r\n" % offs[n]) if n in offs else b"0000000000 65535 f\r\n"
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, x)
    return bytes(out)


# An outline with a good item and a broken one: /Dest [999 0 R /Fit] names an
# object that does not exist (above /Size).
FIXTURE = classic({
    1: b"<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
    2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    4: b"<< /Type /Outlines /First 5 0 R /Last 6 0 R /Count 2 >>",
    5: b"<< /Title (Good) /Parent 4 0 R /Next 6 0 R /Dest [3 0 R /Fit] >>",
    6: b"<< /Title (Broken) /Parent 4 0 R /Prev 5 0 R /Dest [999 0 R /Fit] >>",
})


def build():
    # As the other suites: a copy of the library with an @rpath id (Darwin).
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
    return exe


exe = build()
pdf = os.path.join(WORK, "dangling.pdf")
with open(pdf, "wb") as f:
    f.write(FIXTURE)
cases = [
    # (label, mode, objnum to list, expected)
    ("control: outline never read, save the page", "nowalk", 3, "ok"),
    ("outline read (a Bookmarks panel), save the page", "walk", 3, "refused" if EXPECT_P16 else "ok"),
]
failed = 0
for label, mode, objnum, want in cases:
    r = subprocess.run([exe, pdf, mode, str(objnum)], capture_output=True, text=True)
    got = r.stdout.strip()
    ok = got == want
    failed += not ok
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else " -> got %r, want %r %s" % (got, want, r.stderr.strip())))
print("ALL PASS" if not failed else "%d FAIL" % failed)
sys.exit(1 if failed else 0)
