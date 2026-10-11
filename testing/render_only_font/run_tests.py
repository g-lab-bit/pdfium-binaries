#!/usr/bin/env python3
"""Scripted test for form_render_only.patch (fork-p17): rendering never changes
the document, even when a markup's text needs a fallback font (CJK).

usage: run_tests.py PDFIUM_DIR [--work DIR]
Builds driver.c against the artifact. Exit status 0 = all passed.
"""
import os, platform, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
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
    # A fixed /ID: without one each save makes a new one, and the copies differ.
    out += (b"trailer\n<< /Size %d /Root 1 0 R /ID [<52617069646152656E6465724F6E6C79> <52617069646152656E6465724F6E6C79>] >>"
            b"\nstartxref\n%d\n%%%%EOF\n") % (size, x)
    return bytes(out)


def utf16(s):
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"


def fixture(name, text, acroform):
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R" + (b" /AcroForm 5 0 R" if acroform else b"") + b" >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R] >>",
        4: b"<< /Type /Annot /Subtype /FreeText /Rect [20 20 180 180] /DA (/Helv 12 Tf 0 g) /Contents " + utf16(text) + b" >>",
    }
    if acroform:
        objs[5] = b"<< /Fields [] /DR << /Font << /Helv 6 0 R >> >> /DA (/Helv 0 Tf 0 g) >>"
        objs[6] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(classic(objs))
    return path


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
def field(name, text, need):
    """A text field (widget) holding |text|, no /AP; /NeedAppearances when |need|."""
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R] >>",
        4: b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (title) /Rect [20 80 180 120] /F 4 /P 3 0 R"
           b" /DA (/Helv 12 Tf 0 g) /V " + utf16(text) + b" >>",
        5: b"<< /Fields [4 0 R]" + (b" /NeedAppearances true" if need else b"") +
           b" /DR << /Font << /Helv 6 0 R >> >> /DA (/Helv 0 Tf 0 g) >>",
        6: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(classic(objs))
    return path


CJK = "図面を確認"
cases = [
    ("latin, no /AcroForm", fixture("latin.pdf", "Check beam B-12", False)),
    ("CJK, no /AcroForm", fixture("cjk.pdf", CJK, False)),
    ("CJK, /AcroForm with /DR", fixture("cjk_dr.pdf", CJK, True)),
    ("text field, CJK value, no /AP", field("tx_cjk.pdf", CJK, False)),
    ("text field, CJK value, NeedAppearances", field("tx_cjk_need.pdf", CJK, True)),
]
failed = 0
for label, path in cases:
    r = subprocess.run([exe, path], capture_output=True, text=True)
    out = r.stdout.strip()
    ok = out == "same"
    failed += not ok
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else " -> " + out + " " + r.stderr.strip()))
print("ALL PASS" if not failed else "%d FAIL" % failed)
sys.exit(1 if failed else 0)
