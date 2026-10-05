#!/usr/bin/env python3
"""Scripted test for ocg_view_state.patch, annot_ocmd.patch, annot_ap_oc.patch.

usage: run_tests.py PDFIUM_DIR [--work DIR]
Requires pikepdf (to inspect saved files). Builds driver.c against the
artifact, generates fixtures and checks every case; every case must run.
Exit status 0 = all passed.

Fixture: OCGs WS (index 0, obj 10) and L (index 1, obj 11, nested under WS in
/Order); page content marked /OC L (blue square at 100,100). The driver adds
a Square annotation with a red appearance at 300..400 and puts it on
[WS, L] via FPDFAnnot_SetOCMembership + FPDFAnnot_SetAPOptionalContent.
"""
import json, os, platform, re, shutil, subprocess, sys

try:
    import pikepdf
except ImportError:
    sys.exit("run_tests.py: pikepdf is required (pip install pikepdf)")

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
RED, WHITE, BLUE = [255, 0, 0], [255, 255, 255], [0, 0, 255]


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


def fixture(off):
    content = b"/OC /P1 BDC 0 0 1 rg 50 50 100 100 re f EMC\n"
    return classic({
        1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [10 0 R 11 0 R] "
           b"/D << /Order [10 0 R [11 0 R]] /OFF [%s] >> >> >>" % off,
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
           b"/Resources << /Properties << /P1 11 0 R >> >> >>",
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        10: b"<< /Type /OCG /Name (WS) >>",
        11: b"<< /Type /OCG /Name (L) >>",
    })


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
    drv = lambda *a: json.loads(subprocess.run([exe, *a], capture_output=True, text=True).stdout.strip() or "{}")
    files = {}
    for name, off in (("all_on", b""), ("ws_off", b"10 0 R"), ("l_off", b"11 0 R")):
        src = os.path.join(WORK, name + ".pdf")
        open(src, "wb").write(fixture(off))
        files[name] = os.path.join(WORK, name + ".annotated.pdf")
        r = drv("make", src, files[name])
        if r != {"make": 1, "membership": 1, "ap_oc": 1}:
            sys.exit("setup failed for %s: %s" % (name, r))

    failures, ran = [], 0

    def check(case, ok, detail=""):
        nonlocal ran
        ran += 1
        print("%-44s %s" % (case, "PASS" if ok else "FAIL " + detail))
        if not ok:
            failures.append(case)

    # --- ocg_view_state: rendering (PDFium) ---
    render_cases = [
        # file, view states, flags, expected annot, expected page content
        ("all_on", [], [], RED, BLUE),
        ("all_on", ["0=0"], [], WHITE, BLUE),       # WS off: annot hidden; content is on L only
        ("all_on", ["1=0"], [], WHITE, WHITE),      # L off: both hidden
        ("all_on", ["0=0", "0=-1"], [], RED, BLUE),  # back to following /D
        ("all_on", ["1=0"], ["--clear-after"], RED, BLUE),
        ("all_on", ["1=0"], ["--print"], RED, BLUE),  # Print usage follows the document
        ("ws_off", [], [], WHITE, BLUE),             # /D hides WS
        ("ws_off", ["0=1"], [], RED, BLUE),          # override shows what /D hides
        ("l_off", [], [], WHITE, WHITE),
        ("l_off", ["1=1"], [], RED, BLUE),
    ]
    for fname, states, flags, exp_annot, exp_content in render_cases:
        r = drv("render", files[fname], *states, *flags)
        case = "render %s %s %s" % (fname, " ".join(states) or "-", " ".join(flags))
        check(case, r.get("set_ok") == 1 and r.get("annot") == exp_annot and r.get("content") == exp_content,
              json.dumps(r))
    r = drv("render", files["all_on"], "1=0")
    check("get view state reflects set", r.get("state0") == -1 and r.get("state1") == 0, json.dumps(r))
    r = drv("render", files["all_on"], "5=0")
    check("set view state: invalid index refused", r.get("set_ok") == 0, json.dumps(r))
    r = drv("render", files["all_on"], "1=2")
    check("set view state: invalid state refused", r.get("set_ok") == 0, json.dumps(r))

    # --- ocg_view_state: never reaches the file ---
    r = drv("persist", files["all_on"], os.path.join(WORK, "persist"))
    check("view state not in full save", r.get("set_ok") == 1 and r.get("full_identical") == 1, json.dumps(r))
    check("view state: incremental save writes nothing", r.get("incr_ok") == 1 and r.get("incr_exact_copy") == 1,
          json.dumps(r))
    check("view state: verify TRUE", r.get("verify") == 1, json.dumps(r))
    p = pikepdf.open(os.path.join(WORK, "persist.full_with_override.pdf"))
    d = p.Root.OCProperties.D
    check("view state: /D unchanged", "/ON" not in d and len(d.get("/OFF", [])) == 0, repr(d))

    # --- annot_ap_oc: appearance stream structure ---
    p = pikepdf.open(files["all_on"])
    annot = p.pages[0].Annots[0]
    ap = annot.AP.N
    data = ap.read_bytes()
    props = ap.Resources.Properties
    names = {str(k)[1:]: v.objgen[0] for k, v in props.items()}
    m = re.findall(rb"/OC\s*/(\w+)\s*BDC", data)
    check("AP: nested /OC marks, WS outermost then L",
          [names.get(x.decode()) for x in m[:2]] == [10, 11] and data.count(b"EMC") >= 2, repr(data[:200]))

    # --- annot_ocmd: membership ---
    oc = annot.OC
    check("membership: OCMD /OCGs [WS L] /P /AllOn",
          oc.Type == "/OCMD" and [o.objgen[0] for o in oc.OCGs] == [10, 11] and oc.P == "/AllOn"
          and oc.is_indirect, repr(oc))
    r = drv("membership", files["all_on"], os.path.join(WORK, "member"))
    opened = []  # keep each Pdf alive while its objects are used

    def load(c):
        opened.append(pikepdf.open(os.path.join(WORK, "member.%s.pdf" % c)))
        return opened[-1].pages[0].Annots[0]
    a = load("one")
    check("membership: one -> /OC = ref to L", r["one"]["ret"] == 1 and a.OC.objgen[0] == 11
          and r["one"]["ocg_index"] == 1, json.dumps(r["one"]))
    a = load("two")
    check("membership: two -> new OCMD", r["two"]["ret"] == 1 and a.OC.Type == "/OCMD"
          and [o.objgen[0] for o in a.OC.OCGs] == [10, 11] and r["two"]["ocg_index"] == -1, json.dumps(r["two"]))
    a = load("dup")
    check("membership: duplicates dropped, order kept", r["dup"]["ret"] == 1
          and [o.objgen[0] for o in a.OC.OCGs] == [11, 10], json.dumps(r["dup"]))
    a = load("invalid")
    check("membership: invalid index refused, /OC unchanged", r["invalid"]["ret"] == 0
          and [o.objgen[0] for o in a.OC.OCGs] == [10, 11], json.dumps(r["invalid"]))
    check("membership: negative index refused", r["negative"]["ret"] == 0, json.dumps(r["negative"]))
    a = load("none")
    check("membership: count 0 removes /OC", r["none"]["ret"] == 1 and "/OC" not in a, json.dumps(r["none"]))

    # --- FPDFDoc_DeleteOCG with these OCMDs ---
    out = os.path.join(WORK, "deleted_l.pdf")
    r = drv("delete", files["all_on"], out, "1")
    pd = pikepdf.open(out)
    a = pd.pages[0].Annots[0]
    check("DeleteOCG(L): OCMD pruned to [WS]", r["deleted"] == [1] and a.OC.Type == "/OCMD"
          and [o.objgen[0] for o in a.OC.OCGs] == [10], json.dumps(r))
    check("DeleteOCG(L): AP marks kept (documented)", b"BDC" in a.AP.N.read_bytes())
    out = os.path.join(WORK, "deleted_both.pdf")
    r = drv("delete", files["all_on"], out, "1", "0")
    pd2 = pikepdf.open(out)
    a = pd2.pages[0].Annots[0]
    check("DeleteOCG(L, WS): /OC removed", r["deleted"] == [1, 1] and "/OC" not in a, json.dumps(r))

    # --- annot_ap_oc: removal and refusals ---
    out = os.path.join(WORK, "unmarked.pdf")
    r = drv("unmark", files["l_off"], out)
    check("SetAPOptionalContent(count 0) succeeds; other modes and bad index refused",
          r == {"unmark": 1, "rollover": 0, "bad_index": 0}, json.dumps(r))
    pu = pikepdf.open(out)
    data = pu.pages[0].Annots[0].AP.N.read_bytes()
    check("unmarked AP has no /OC", b"/OC" not in data, repr(data[:120]))
    r = drv("render", out)
    check("unmarked: visible although L is off in /D", r.get("annot") == RED, json.dumps(r))

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
