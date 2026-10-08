#!/usr/bin/env python3
"""Scripted test for annot_undo_wrap.patch (fork-p13): exact annotation undo and
FPDFAnnot_WrapAppearance.

usage: run_tests.py PDFIUM_DIR [--work DIR]
Requires pikepdf. Builds driver.c against the artifact; every case must run.
Exit status 0 = all passed.
"""
import json, os, platform, shutil, subprocess, sys

try:
    import pikepdf
except ImportError:
    sys.exit("run_tests.py: pikepdf is required")
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


def stream(data, extra=b""):
    return b"<< /Length %d %s >>\nstream\n" % (len(data), extra) + data + b"endstream"


PAGES = {2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"}
EDIT = {1: b"<< /Type /Catalog /Pages 2 0 R >>", **PAGES,
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> "
           b"/Annots [10 0 R << /Type /Annot /Subtype /Square /Rect [20 20 60 60] /C [0 1 0] /F 4 >> "
           b"<< /Type /Annot /Subtype /Circle /Rect [220 20 260 60] /C [0 0 1] /F 4 >>] >>",
        10: b"<< /Type /Annot /Subtype /Square /Rect [100 100 200 150] /C [1 0 0] /F 4 /Contents (orig) >>"}
EMPTY = {1: b"<< /Type /Catalog /Pages 2 0 R >>", **PAGES,
         3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> >>"}
WRAP = {1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [30 0 R] /D << /Order [30 0 R] >> >> >>",
        **PAGES,
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> /Annots [10 0 R] >>",
        10: b"<< /Type /Annot /Subtype /Stamp /Rect [100 100 200 150] /F 4 /AP << /N 20 0 R >> >>",
        20: stream(b"1 0 0 rg 0 0 100 50 re f\n", b"/Type /XObject /Subtype /Form /BBox [0 0 100 50] /Resources << >>"),
        30: b"<< /Type /OCG /Name (Other tool layer) >>"}


def variant(form, page_extra=b"", subtype=b"/Stamp", contents=None):
    objs = dict(WRAP)
    objs[20] = form
    objs[10] = b"<< /Type /Annot /Subtype %s /Rect [100 100 200 150] /F 4 /AP << /N 20 0 R >> >>" % subtype
    if contents is not None:
        objs[3] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> "
                   b"/Contents 40 0 R /Annots [10 0 R] >>")
        objs[40] = stream(contents)
    return objs


FORM = b"/Type /XObject /Subtype /Form "
# Left half drawn, BBox not at the origin (soft mask + BBox mapping).
HALF = variant(stream(b"1 0 0 rg 10 10 50 50 re f\n", FORM + b"/BBox [10 10 110 60] /Resources << >>"))
# Rotated by /Matrix: the drawn half lands on the RIGHT half of the /Rect.
ROT = variant(stream(b"1 0 0 rg 0 0 50 50 re f\n", FORM + b"/BBox [0 0 50 100] /Matrix [0 1 -1 0 0 0] /Resources << >>"))
# Another tool's highlight: yellow, /BM /Multiply over black page "text".
HL = variant(stream(b"/G gs 1 1 0 rg 0 0 100 50 re f\n",
                    FORM + b"/BBox [0 0 100 50] /Resources << /ExtGState << /G << /BM /Multiply >> >> >>"),
             subtype=b"/Highlight", contents=b"0 0 0 rg 100 115 100 20 re f\n")


def build():
    os.makedirs(WORK, exist_ok=True)
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


def main():
    exe = build()
    def drv(*a):
        out = {}
        for line in subprocess.run([exe, *a], capture_output=True, text=True).stdout.splitlines():
            if line.strip():
                out.update(json.loads(line))
        return out
    files = {}
    for name, objs in (("edit", EDIT), ("empty", EMPTY), ("wrap", WRAP), ("half", HALF), ("rot", ROT), ("hl", HL)):
        files[name] = os.path.join(WORK, name + ".pdf")
        open(files[name], "wb").write(classic(objs))
    failures, ran = [], 0

    def check(case, ok, detail=""):
        nonlocal ran
        ran += 1
        print("%-52s %s" % (case, "PASS" if ok else "FAIL " + detail))
        if not ok:
            failures.append(case)

    r = drv("restore", files["edit"])
    d = json.dumps(r)
    check("state: save returns ids > 0", all(i > 0 for i in r.get("ids", [0])), d)
    check("state: the edits change both holders", r.get("changed") == 2, d)
    check("state: restore indirect + direct annotation", r.get("restored") == [1, 1], d)
    check("state: after restore nothing is changed (filter)", r.get("after") == 0, d)
    check("state: a direct state on an indirect annot refused", r.get("wrong") == 0, d)
    check("state: unknown id refused", r.get("bad") == 0, d)
    check("state: a state can be restored again", r.get("again") == 1, d)
    check("state: released state is gone", r.get("released") == 0, d)
    check("state: another direct annotation's state refused", r.get("wrong_direct") == 0, d)
    check("state: restore reports the annotation as modified", r.get("reported") == 1, d)

    r = drv("reinsert", files["edit"])
    d = json.dumps(r)
    check("reinsert: removing changes the page", r.get("removed") == 1, d)
    check("reinsert: back at their positions", r.get("inserted") == [0, 1], d)
    check("reinsert: the same object twice refused", r.get("twice") == -1, d)
    check("reinsert: a state is put back once", r.get("twice_direct") == -1, d)
    check("reinsert: reports the page as modified", r.get("reported") == 1, d)
    check("reinsert: page and annotation unchanged (filter)", r.get("after") == 0, d)
    check("reinsert: indirect annotation is the same object", r.get("obj0") == 10 and r.get("count") == 3, d)

    r = drv("empty", files["empty"])
    d = json.dumps(r)
    check("empty /Annots: an added annotation changes the page", r.get("with_annot") == 1, d)
    check("empty /Annots: added + removed = unchanged", r.get("after_remove") == 0, d)

    RED = lambda p: p[0] > 200 and p[2] < 60
    BLUISH = lambda p: p[2] > p[0] + 40
    WHITE = lambda p: min(p) > 240
    opened = []

    def ap_n(path):
        pdf = pikepdf.open(path)
        opened.append(pdf)
        return pdf.pages[0].Annots[0].AP.N
    expect = {
        "tint": (BLUISH, BLUISH, True, False),
        "layer": (RED, WHITE, False, True),
        "both": (BLUISH, WHITE, True, True),
        "unwrap": (RED, RED, None, None),
        "rewrap": (BLUISH, WHITE, True, True),
    }
    for mode, (on_ok, off_ok, tinted, marked) in expect.items():
        out = os.path.join(WORK, "wrap_%s.pdf" % mode)
        r = drv("wrap", files["wrap"], out, mode)
        d = json.dumps(r)
        check("wrap %s: done; bad colour and bad layer refused" % mode,
              r.get("ok") == 1 and r.get("bad_rgb") == 0 and r.get("bad_layer") == 0 and r.get("saved") == 1, d)
        check("wrap %s: layer on renders as expected" % mode, on_ok(r.get("on", [0, 0, 0])), d)
        check("wrap %s: layer off renders as expected" % mode, off_ok(r.get("off", [0, 0, 0])), d)
        n = ap_n(out)
        if mode == "unwrap":
            check("unwrap: /AP /N is the original form again", n.objgen[0] == 20, repr(n.objgen))
            continue
        data = n.read_bytes()
        res = n.Resources
        ok = (n.objgen[0] != 20 and n.get("/RpOriginal") is not None and n.RpOriginal.objgen[0] == 20
              and res.XObject.O.objgen[0] == 20 and b"/O Do" in data)
        check("wrap %s: wrapper draws the original form unchanged" % mode, ok, repr(data[:120]))
        if tinted:
            tn = res.XObject.Tn
            ok = (res.ExtGState.B.BM == "/Color" and tn.Resources.ExtGState.M.SMask.S == "/Alpha"
                  and tn.Resources.ExtGState.M.SMask.G.Resources.XObject.O.objgen[0] == 20
                  and "/BM" not in tn.Resources.ExtGState.M)
            check("wrap %s: tint = soft mask of the original, /Color blend apart" % mode, ok)
        if marked:
            ok = data.count(b"BDC") == 1 and data.count(b"EMC") == 1 and res.Properties.RpOC1.objgen[0] == 30
            check("wrap %s: /OC marks for the layer" % mode, ok, repr(data[:120]))
    # Geometry and blending against originals that are not a full opaque box.
    DARK = lambda p: max(p) < 90
    cases = [
        ("half", "tint", lambda r: BLUISH(r["left"]) and WHITE(r["right"]), "soft mask: only the drawn half is tinted (BBox offset)"),
        ("half", "layer", lambda r: RED(r["left"]) and WHITE(r["right"]) and WHITE(r["off"]), "BBox offset kept; hidden with its layer"),
        ("rot", "tint", lambda r: BLUISH(r["right"]) and WHITE(r["left"]), "rotated /Matrix: tint on the drawn (right) half"),
        ("rot", "unwrap", lambda r: RED(r["right"]) and WHITE(r["left"]), "rotated /Matrix: original back after unwrap"),
        ("hl", "both", lambda r: DARK(r["on"]) and DARK(r["off"]), "Multiply highlight: text stays visible when tinted"),
        ("hl", "layer", lambda r: DARK(r["on"]), "Multiply highlight: text stays visible when layer-marked"),
    ]
    for fname, mode, ok, label in cases:
        r = drv("wrap", files[fname], os.path.join(WORK, "wrap_%s_%s.pdf" % (fname, mode)), mode)
        check("wrap %s %s: %s" % (fname, mode, label), r.get("ok") == 1 and ok(r), json.dumps(r))
    # The wrapper is not a transparency group (blend modes act on the page).
    w = ap_n(os.path.join(WORK, "wrap_hl_both.pdf"))
    check("wrap: the wrapper is not a transparency group", "/Group" not in w, repr(w.keys()))
    r = drv("wrap", files["wrap"], os.path.join(WORK, "wrap_noop.pdf"), "noop")
    check("wrap: removing a wrapper that is not there changes nothing", r.get("ok") == 1 and r.get("noop_kept") == 0, json.dumps(r))
    # The original form object is never modified.
    orig = opened[0] if opened else None
    if orig is not None:
        f20 = orig.get_object(20, 0)
        check("wrap: the original form is untouched", f20.read_bytes() == b"1 0 0 rg 0 0 100 50 re f\n")

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
