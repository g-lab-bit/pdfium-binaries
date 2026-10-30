#!/usr/bin/env python3
"""Scripted test for fork_p18.patch (Rapida ra-1okhu, ra-c2kyq.12, ra-4sj5g.1):
  - FPDF_GetForkVersion names this fork release;
  - FPDFAnnot_SetRenderHidden leaves one annotation out of renders, render-only
    (a save while it is hidden is byte-identical), until unhidden or cleared;
  - FPDF_FFLDraw draws a widget's appearance with the render's optional-content
    context: marked content in an OCG that is off is not drawn, and the
    document's view state (FPDFDoc_SetOCGViewState) turns it back on.

usage: run_tests.py PDFIUM_DIR [--work DIR] [--expect-p17]
Builds driver.c against the artifact. Exit status 0 = all passed.
--expect-p17: a p17 artifact has none of this; the driver does not even link,
which is reported as the expected failure (proves the suite needs p18).
"""
import os, platform, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
EXPECT_P17 = "--expect-p17" in sys.argv
os.makedirs(WORK, exist_ok=True)


def stream(dict_body, data):
    return b"<< " + dict_body + b" /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


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


# A red square annotation at [20 20 80 80], and a push button widget at
# [120 120 180 180] whose appearance has a blue bar (no optional content) at
# its bottom and a red area marked /OC /oc1, an OCG that /D turns off for
# viewing and its /Usage /Print turns on for printing (/AS Print event).
FIXTURE = classic({
    1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [7 0 R] >>"
       b" /OCProperties << /OCGs [9 0 R] /D << /OFF [9 0 R]"
       b" /AS [<< /Event /Print /OCGs [9 0 R] /Category [/Print] >>] >> >> >>",
    2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [5 0 R 7 0 R] >>",
    5: b"<< /Type /Annot /Subtype /Square /Rect [20 20 80 80] /F 4 /AP << /N 6 0 R >> >>",
    6: stream(b"/Type /XObject /Subtype /Form /BBox [0 0 60 60]", b"1 0 0 rg 0 0 60 60 re f"),
    7: b"<< /Type /Annot /Subtype /Widget /FT /Btn /Ff 65536 /T (b1) /Rect [120 120 180 180]"
       b" /F 4 /P 3 0 R /AP << /N 8 0 R >> >>",
    8: stream(b"/Type /XObject /Subtype /Form /BBox [0 0 60 60] /Resources << /Properties << /oc1 9 0 R >> >>",
              b"0 0 1 rg 0 0 60 10 re f /OC /oc1 BDC 1 0 0 rg 0 20 60 40 re f EMC"),
    9: b"<< /Type /OCG /Name (Layer 1) /Usage << /Print << /PrintState /ON >> >> >>",
})

RED, WHITE, BLUE = "255,0,0", "255,255,255", "0,0,255"
WANT = {
    "version": "chromium/8076-fork-p18",
    "square.shown": RED,
    "hide.ok": "1",
    "square.hidden": WHITE,
    "save.same": "1",
    "square.again": RED,
    "square.cleared": RED,
    "hide.badargs": "0",
    "widget.plain": BLUE,
    "widget.oc.default": WHITE,
    "widget.oc.on": RED,
    "widget.oc.view": WHITE,
    "widget.oc.print": RED,
    "widget.hidden": WHITE,
    "widget.again": BLUE,
    "square.reloaded": WHITE,
}


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
    r = subprocess.run(["cc", "-O1", "-I", os.path.join(PD, "include"), os.path.join(HERE, "driver.c"),
                        "-L", lib, "-lpdfium", "-Wl,-rpath," + lib, "-o", exe], capture_output=True, text=True)
    return exe if r.returncode == 0 else None


exe = build()
if exe is None:
    if EXPECT_P17:
        print("PASS the driver does not build against a pre-p18 artifact (no fork-p18 API)")
        print("ALL PASS")
        sys.exit(0)
    print("FAIL could not build the driver against %s" % PD)
    sys.exit(1)

pdf = os.path.join(WORK, "fork_p18.pdf")
with open(pdf, "wb") as f:
    f.write(FIXTURE)
r = subprocess.run([exe, pdf], capture_output=True, text=True)
got = dict(line.split(" ", 1) for line in r.stdout.strip().splitlines() if " " in line)
# Render-only: a save while the square is hidden equals one without (the
# trailer /ID aside: it is random per save).
strip_id = lambda b: re.sub(rb"/ID\s*\[<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\]", b"", b)
try:
    saves = [open(pdf + ".%s.pdf" % k, "rb").read() for k in ("before", "during")]
    got["save.same"] = "1" if saves[0] and strip_id(saves[0]) == strip_id(saves[1]) else "0"
except OSError:
    got["save.same"] = "missing"
failed = 0
for key, want in WANT.items():
    ok = got.get(key) == want
    failed += not ok
    print(("PASS " if ok else "FAIL ") + key + ("" if ok else " -> got %r, want %r" % (got.get(key), want)))
if r.returncode != 0:
    failed += 1
    print("FAIL driver exit %d: %s" % (r.returncode, r.stderr.strip()[:300]))
print("ALL PASS" if not failed else "%d FAIL" % failed)
sys.exit(1 if failed else 0)
