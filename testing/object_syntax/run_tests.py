#!/usr/bin/env python3
"""Scripted test for object_syntax_api.patch and annot_tracker_scope.patch
(fork-p16).

usage: run_tests.py PDFIUM_DIR [--work DIR]
Requires pikepdf (to read the saved file back). Builds driver.c against the
artifact, generates the fixture and checks every case; exit status 0 = all
passed.
"""
import json, os, platform, shutil, subprocess, sys

try:
    import pikepdf
except ImportError:
    sys.exit("run_tests.py: pikepdf is required (pip install pikepdf)")

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


def fixture():
    return classic({
        1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [10 0 R] /D << /Order [10 0 R] >> >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        10: b"<< /Type /OCG /Name (Layer) >>",
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
    src = os.path.join(WORK, "f.pdf")
    open(src, "wb").write(fixture())
    failures, ran = [], 0

    def check(case, ok, detail=""):
        nonlocal ran
        ran += 1
        print("%-60s %s" % (case, "PASS" if ok else "FAIL " + detail))
        if not ok:
            failures.append(case)

    # --- object_syntax_api ---
    out = os.path.join(WORK, "syntax.pdf")
    r = drv("syntax", src, out)
    check("set /CL, /IT, /Measure (annotation) and /VP (page)",
          r.get("cl") == 1 and r.get("it") == 1 and r.get("measure") == 1 and r.get("vp") == 1, json.dumps(r))
    check("get /CL back as syntax", r.get("cl_text", "").replace(" ", "") == "[100200150250]"
          or r.get("cl_text") == "[100 200 150 250]", json.dumps(r.get("cl_text")))
    check("get /Measure back (sized call, then filled)",
          r.get("measure_len", 0) > 20 and "/Measure" in r.get("measure_text", "") and "/RL" in r.get("measure_text", ""),
          json.dumps(r.get("measure_text")))
    for k in ("ref1", "ref2", "junk", "reserved", "open_str", "null", "page_reserved", "stream"):
        check("refused: " + k, r.get(k) == 0, json.dumps(r))
    check("a refused key was not written", r.get("x1_after") == 0, json.dumps(r))
    check("\"\" removes the key", r.get("removed") == 1 and r.get("it_after") == 0, json.dumps(r))
    check("saved", r.get("saved") == 1, json.dumps(r))
    pdf = pikepdf.open(out)
    page = pdf.pages[0].obj
    annot = page.Annots[0]
    check("reopened: /CL", [float(v) for v in annot.get("/CL", [])] == [100, 200, 150, 250], repr(annot.get("/CL")))
    check("reopened: /IT removed", "/IT" not in annot, repr(annot.keys()))
    m = annot.get("/Measure")
    check("reopened: /Measure dict", m is not None and m.get("/Subtype") == "/RL" and str(m.get("/R")) == "1 in = 10 ft"
          and float(m.X[0].C) == 0.8333, repr(m))
    vp = page.get("/VP")
    check("reopened: page /VP", vp is not None and len(vp) == 1 and str(vp[0].Name) == "Plan"
          and str(vp[0].Measure.R) == "1:100", repr(vp))
    check("reopened: /Subtype untouched", annot.get("/Subtype") == "/Square", repr(annot.get("/Subtype")))

    # --- annot_tracker_scope: an annotation edit costs O(1) in the page's
    # annotations (it serialized every inline annotation before and after) ---
    a = drv("tracker", src, "500")
    b = drv("tracker", src, "2000")
    check("tracker: every SetOCMembership succeeded", a.get("ok") == 1 and b.get("ok") == 1, json.dumps([a, b]))
    check("tracker: reports the page (holder of the direct annotations)",
          b.get("nmod") == 1 and b.get("mod0") == b.get("page") and b.get("page") == 3, json.dumps(b))
    ratio = (b.get("ms", 0) or 0) / max(a.get("ms", 0) or 0.1, 0.1)
    check("tracker: 4x the annotations ~ 4x the time (was ~16x)", ratio < 8.0,
          "500: %.1f ms, 2000: %.1f ms, ratio %.1f" % (a.get("ms", 0), b.get("ms", 0), ratio))

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
