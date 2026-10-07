#!/usr/bin/env python3
"""Scripted test for ocg_view_state.patch, annot_ocmd.patch, annot_ap_oc.patch,
annot_oc_membership.patch.

usage: run_tests.py PDFIUM_DIR [--work DIR]
Requires pikepdf (to inspect saved files). Builds driver.c against the
artifact, generates fixtures and checks every case; every case must run.
Exit status 0 = all passed.

Fixture: OCGs WS (index 0, obj 10) and L (index 1, obj 11, nested under WS in
/Order); page content marked /OC L (blue square at 100,100). The driver adds
a Square annotation with a red appearance at 300..400 and puts it on
[WS, L] via FPDFAnnot_SetOCMembership + FPDFAnnot_SetAPOptionalContent.
"""
import io, json, os, platform, re, shutil, subprocess, sys

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
    # annot_oc_membership: what was written reads back (invalid/negative leave
    # the fixture's [WS, L]; count 0 has no /OC).
    expect_get = {"one": [1, [1]], "two": [2, [0, 1]], "dup": [2, [1, 0]], "invalid": [2, [0, 1]],
                  "negative": [2, [0, 1]], "none": [0, []]}
    for c, want in expect_get.items():
        check("get membership after set: " + c, r[c].get("get") == want, json.dumps(r[c]))
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
    check("DeleteOCG(L): membership reads [WS]", r.get("member") == [1, [0]], json.dumps(r))
    out = os.path.join(WORK, "deleted_both.pdf")
    r = drv("delete", files["all_on"], out, "1", "0")
    pd2 = pikepdf.open(out)
    a = pd2.pages[0].Annots[0]
    check("DeleteOCG(L, WS): /OC removed", r["deleted"] == [1, 1] and "/OC" not in a, json.dumps(r))
    check("DeleteOCG(L, WS): membership reads none", r.get("member") == [0, []], json.dumps(r))

    # --- annot_oc_membership: /OC written by other tools ---
    src = os.path.join(WORK, "foreign_oc.pdf")
    pf = pikepdf.open(io.BytesIO(fixture(b"")))
    ws, l = pf.get_object(10, 0), pf.get_object(11, 0)
    stray = pf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Stray"))   # not in /OCGs
    N = pikepdf.Name
    ocmd = lambda **kw: pf.make_indirect(pikepdf.Dictionary(Type=N.OCMD, **kw))
    variants = [
        ("ocg", l, [1, [1]]),
        ("all_on", ocmd(OCGs=pikepdf.Array([ws, l]), P=N.AllOn), [2, [0, 1]]),
        ("single_ref_any_on", ocmd(OCGs=l), [1, [1]]),
        ("any_on_two", ocmd(OCGs=pikepdf.Array([ws, l])), [-1, []]),
        ("all_off_two", ocmd(OCGs=pikepdf.Array([ws, l]), P=N.AllOff), [-1, []]),
        ("ve", ocmd(OCGs=pikepdf.Array([ws, l]), P=N.AllOn, VE=pikepdf.Array([N.And, ws, l])), [-1, []]),
        ("not_listed", ocmd(OCGs=pikepdf.Array([ws, stray]), P=N.AllOn), [-1, []]),
        ("direct_ocg", pikepdf.Dictionary(Type=N.OCG, Name="Direct"), [-1, []]),
        ("none", None, [0, []]),
        ("dup_members", ocmd(OCGs=pikepdf.Array([l, l]), P=N.AllOn), [1, [1]]),
    ]
    annots = pikepdf.Array()
    for k, (_, oc, _) in enumerate(variants):
        d = pikepdf.Dictionary(Type=N.Annot, Subtype=N.Square, Rect=[10 + 20 * k, 500, 25 + 20 * k, 515])
        if oc is not None:
            d.OC = oc
        annots.append(pf.make_indirect(d))
    pf.pages[0].Annots = annots
    pf.save(src)
    r = drv("getmember", src)
    for k, (name, _, want) in enumerate(variants):
        got = r.get(str(k), {})
        n = want[0]
        ok = (got.get("member") == want and got.get("sized") == n
              and got.get("partial") == ([n, want[1][0]] if n > 0 else [n, -9]))
        check("get membership: " + name, ok, json.dumps(got))
    check("get membership: bad arguments -> -1",
          r.get("bad_annot") == -1 and r.get("neg_len") == -1 and r.get("null_buf") == -1, json.dumps(r))

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

    # --- incremental save + verify (S1) ---
    GREEN = [0, 255, 0]

    def ap_info(path):
        pdf = pikepdf.open(path)
        a = pdf.pages[0].Annots[0]
        ap = a.AP.N
        props = ap.Resources.get("/Properties", {})
        names = {str(k)[1:]: (v.objgen[0] if v.is_indirect else None) for k, v in props.items()}
        marks = [x.decode() for x in re.findall(rb"/OC\s*/(\w+)\s*BDC", ap.read_bytes())]
        return pdf, a, ap, names, marks

    raw = os.path.join(WORK, "all_on.pdf")
    scen = [
        # name, source, scenario, indices, render expectations [(states, annot)]
        ("new annotation", raw, "new", ["0", "1"],
         [([], RED), (["1=0"], WHITE), (["0=0"], WHITE)]),
        ("existing annotation, AP rebuilt", files["all_on"], "rebuilt", ["1"],
         [([], GREEN), (["1=0"], WHITE), (["0=0"], GREEN)]),
        ("existing annotation, reloaded AP re-marked", files["all_on"], "reloaded", ["0"],
         [([], RED), (["0=0"], WHITE), (["1=0"], RED)]),
        ("re-marked twice ([WS,L] then [L])", files["all_on"], "twice", ["1"],
         [([], RED), (["1=0"], WHITE), (["0=0"], RED)]),
    ]
    for name, src, scenario, idx, renders in scen:
        out = os.path.join(WORK, "incr_%s.pdf" % scenario)
        r = drv("incr", src, out, scenario, *idx)
        check("incremental + verify: " + name,
              all(r.get(k) == 1 for k in ("edit", "membership", "marked", "saved", "verify", "prefix")),
              json.dumps(r))
        if r.get("saved") != 1:
            continue
        for states, exp in renders:
            rr = drv("render", out, *states)
            check("  render %s %s" % (scenario, " ".join(states) or "-"), rr.get("annot") == exp, json.dumps(rr))
    pdf, a, ap, names, marks = ap_info(os.path.join(WORK, "incr_twice.pdf"))
    check("twice: stale /RpOC entry for WS removed, one mark for L",
          list(names.values()) == [11] and marks == [k for k in names] and len(marks) == 1,
          "%s %s" % (names, marks))
    pdf, a, ap, names, marks = ap_info(os.path.join(WORK, "incr_rebuilt.pdf"))
    orig_pdf = pikepdf.open(files["all_on"])
    old_ap = orig_pdf.pages[0].Annots[0].AP.N.objgen[0]
    check("rebuilt: /AP /N is a new stream object", ap.objgen[0] != old_ap, "%s vs %s" % (ap.objgen, old_ap))

    # --- /Properties name collisions (S3) ---
    coll = os.path.join(WORK, "collision.pdf")
    pdf = pikepdf.open(files["all_on"])
    ap = pdf.pages[0].Annots[0].AP.N
    foo = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Foo")))
    ap.Resources.Properties = pikepdf.Dictionary(RpOC1=foo, Foo=pdf.get_object(10, 0))
    pdf.save(coll)
    out = os.path.join(WORK, "collision_marked.pdf")
    r = drv("incr", coll, out, "reloaded", "0", "1")
    pdf, a, ap, names, marks = ap_info(out)
    # pikepdf renumbered the objects when writing the fixture: look the
    # groups up by index.
    ws_num, l_num = [o.objgen[0] for o in pdf.Root.OCProperties.OCGs]
    check("collision: WS reuses /Foo, L gets an unused /RpOC2, /RpOC1 kept",
          r.get("verify") == 1 and marks == ["Foo", "RpOC2"] and names.get("RpOC2") == l_num
          and names.get("Foo") == ws_num and "RpOC1" in names, "%s %s %s" % (json.dumps(r), names, marks))
    out2 = os.path.join(WORK, "collision_unmarked.pdf")
    r = drv("incr", out, out2, "reloaded")
    pdf, a, ap, names, marks = ap_info(out2)
    check("collision: count 0 removes only our /RpOC2; /RpOC1 (not an OCG) and /Foo kept",
          r.get("verify") == 1 and marks == [] and sorted(names) == ["Foo", "RpOC1"],
          "%s %s %s" % (json.dumps(r), names, marks))

    # --- the form follows the new stream; the old stream is never touched ---
    def stream_snapshot(path, objnum):
        pdf = pikepdf.open(path)
        obj = pdf.get_object(objnum, 0)
        return pdf, obj.read_raw_bytes(), repr(dict(obj.stream_dict.items()))

    rich = os.path.join(WORK, "rich.pdf")
    r = drv("rich", raw, rich)
    check("rich fixture (transparent path + text, unmarked)", r.get("rich") == 1, json.dumps(r))
    rich_pdf, _, ap0, _, _ = ap_info(rich)  # keep the Pdf alive
    old_num = ap0.objgen[0]
    _, old_data, old_dict = stream_snapshot(rich, old_num)

    # (1) mark, then append text + change opacity WITHOUT re-marking
    out = os.path.join(WORK, "markappend.pdf")
    r = drv("markappend", files["all_on"], out, "0", "1")
    check("mark then append/update without re-marking: incremental + verify TRUE",
          all(r.get(k) == 1 for k in ("marked", "appended", "updated", "saved", "verify")), json.dumps(r))
    pdf, a, ap, names, marks = ap_info(out)
    res = ap.Resources
    data = ap.read_bytes()
    check("  new stream holds the Font and ExtGState it uses",
          "/Font" in res and "/ExtGState" in res and b"Tf" in data and b" gs" in data,
          "%s %r" % (list(res.keys()), data[:160]))
    first_pdf = pikepdf.open(files["all_on"])
    first_ap = first_pdf.pages[0].Annots[0].AP.N
    _, d2, dict2 = stream_snapshot(out, first_ap.objgen[0])
    check("  original stream unchanged (data and dictionary)",
          ap.objgen != first_ap.objgen and d2 == first_ap.read_raw_bytes()
          and dict2 == repr(dict(first_ap.stream_dict.items())), "objgen %s" % (ap.objgen,))
    for states, exp in (([], [255, 127, 127]), (["1=0"], WHITE)):
        rr = drv("render", out, *states)
        px = rr.get("annot", [0, 0, 0])
        ok = (px == WHITE) if exp == WHITE else (px[0] == 255 and 110 <= px[1] <= 145 and 110 <= px[2] <= 145)
        check("  render markappend %s (semi-transparent red / hidden)" % (" ".join(states) or "-"), ok, json.dumps(rr))

    # (2) reloaded appearance that needs resources, re-marked: generation
    # realises them in the new stream; the original stream is unchanged
    out = os.path.join(WORK, "rich_marked.pdf")
    r = drv("incr", rich, out, "reloaded", "0", "1")
    check("reloaded appearance needing resources: incremental + verify TRUE",
          r.get("saved") == 1 and r.get("verify") == 1, json.dumps(r))
    _, d2, dict2 = stream_snapshot(out, old_num)
    check("  original stream unchanged (serialized before/after)",
          d2 == old_data and dict2 == old_dict)
    pdf, a, ap, names, marks = ap_info(out)
    data = ap.read_bytes()
    check("  new stream: marks + Font + ExtGState",
          marks and "/Font" in ap.Resources and "/ExtGState" in ap.Resources and b"Tf" in data,
          "%s %s" % (marks, list(ap.Resources.keys())))

    # --- view state through every render path (incl. FFLDraw) ---
    wfile = os.path.join(WORK, "widget.pdf")
    pdf = pikepdf.open(files["all_on"])
    content = b"/OC /P1 BDC 0 1 0 rg 0 0 100 50 re f EMC"
    wap = pdf.make_stream(content)
    wap.Type = pikepdf.Name.XObject; wap.Subtype = pikepdf.Name.Form
    wap.BBox = [0, 0, 100, 50]
    wap.Resources = pikepdf.Dictionary(Properties=pikepdf.Dictionary(P1=pdf.get_object(11, 0)))
    widget = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name.Tx,
        T=pikepdf.String("w1"), Rect=[100, 500, 200, 550], F=4, P=pdf.pages[0].obj,
        AP=pikepdf.Dictionary(N=wap)))
    pdf.pages[0].Annots.append(widget)
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[widget])
    pdf.save(wfile)
    # FPDF_RenderPageBitmap never draws widgets (stock: bShowWidget = false);
    # FPDF_FFLDraw draws them through CPDF_Annot::DrawAppearance, which
    # renders WITHOUT an optional content context (stock), so the widget's
    # /OC marked content is ignored there for /D and the view state alike.
    # The checks pin that measured behaviour; the page is drawn without
    # FPDF_ANNOT for FFLDraw, so the markup is not on that bitmap.
    for states, on in (([], True), (["1=0"], False)):
        r = drv("paths", wfile, *states)
        for path in ("render", "progressive", "render_lod", "progressive_lod", "ffldraw"):
            d = r.get(path, {})
            if path == "ffldraw":
                ok = (d.get("annot") == WHITE and d.get("content") == (BLUE if on else WHITE)
                      and d.get("widget") == GREEN)
                label = "view state %s via ffldraw (page content follows; widget OC ignored, stock)"
            else:
                ok = (d.get("annot") == (RED if on else WHITE) and d.get("content") == (BLUE if on else WHITE)
                      and d.get("widget") == WHITE)
                label = "view state %s via " + path
            check(label % (" ".join(states) or "-"), ok, json.dumps(d))

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
