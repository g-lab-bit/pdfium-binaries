#!/usr/bin/env python3
"""Scripted test for ocg_objnums.patch and verify_empty_stream.patch.

usage: run_tests.py PDFIUM_DIR [--work DIR]
Builds driver.c against the artifact, generates fixtures (stdlib only) and
checks every case; every case must run. Exit status 0 = all passed.

For every fork OCG/annotation write call and every fixture layout:
  - FPDFDoc_GetLastModifiedObjects is non-empty and the call succeeded;
  - FPDF_SaveIncrementalObjects listing exactly the reported objects +
    FPDF_VerifyIncrementalSave is TRUE;
  - leaving out ANY reported object makes the save refuse or verify FALSE.
Together: the report is sufficient and every entry necessary (exact).
"""
import json, os, platform, shutil, subprocess, sys, zlib

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


def xref_stream(objs, members, root=1):
    """xref stream + one ObjStm holding `members` (non-stream objects)."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offs, comp = {}, {}
    for n in sorted(objs):
        if n not in members:
            offs[n] = len(out)
            out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    stm = max(objs) + 1
    hdr, body = b"", b""
    for i, n in enumerate(members):
        hdr += b"%d %d " % (n, len(body))
        body += objs[n] + b"\n"
        comp[n] = (stm, i)
    data = zlib.compress(hdr + body)
    offs[stm] = len(out)
    out += (b"%d 0 obj\n<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n"
            % (stm, len(members), len(hdr), len(data)) + data + b"\nendstream\nendobj\n")
    xn = stm + 1
    offs[xn] = len(out)
    rows = b"\x00\x00\x00\x00\xff\xff"
    for n in range(1, xn + 1):
        if n in comp:
            rows += b"\x02" + comp[n][0].to_bytes(4, "big") + comp[n][1].to_bytes(1, "big")
        elif n in offs:
            rows += b"\x01" + offs[n].to_bytes(4, "big") + b"\x00"
        else:
            rows += b"\x00\x00\x00\x00\x00\x00"
    out += (b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 1] /Root %d 0 R /Length %d >>\nstream\n"
            % (xn, xn + 1, root, len(rows)) + rows + b"\nendstream\nendobj\n")
    out += b"startxref\n%d\n%%%%EOF\n" % offs[xn]
    return bytes(out)


AP = b"1 0 0 rg 300 300 100 100 re f\n"
OCPROPS = b"<< /OCGs [10 0 R 11 0 R] /D << /ON [10 0 R 11 0 R] /OFF [] /Order [10 0 R [11 0 R]] >> >>"
DIRECT_ANNOT = b"<< /Type /Annot /Subtype /Square /Rect [100 100 150 150] /F 4 /OC 11 0 R >>"


def base(catalog_ocprops, annots=b"[30 0 R %s]" % DIRECT_ANNOT):
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties %s >>" % catalog_ocprops,
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> /Annots %s >>" % annots,
        4: b"<< /Length 0 >>\nstream\n\nendstream",
        10: b"<< /Type /OCG /Name (WS) >>",
        11: b"<< /Type /OCG /Name (L) >>",
        30: b"<< /Type /Annot /Subtype /Square /Rect [300 300 400 400] /F 4 /OC 31 0 R /AP << /N 40 0 R /D 41 0 R >> >>",
        31: b"<< /Type /OCMD /OCGs [10 0 R 11 0 R] /P /AllOn >>",
        40: b"<< /Type /XObject /Subtype /Form /BBox [300 300 400 400] /Resources << >> /Length %d >>\nstream\n" % len(AP) + AP + b"endstream",
        41: b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Length 0 >>\nstream\n\nendstream",
    }


def fixtures():
    f = {}
    o = base(b"20 0 R"); o[20] = OCPROPS
    f["indirect"] = classic(o)
    f["direct_in_catalog"] = classic(base(OCPROPS))
    o = base(b"20 0 R"); o[20] = OCPROPS
    f["objstm"] = xref_stream(o, members=(1, 2, 3, 10, 11, 20, 30, 31))
    o = base(b"20 0 R", annots=b"32 0 R")
    o[20] = b"<< /OCGs 21 0 R /D 22 0 R >>"
    o[21] = b"[10 0 R 11 0 R]"
    o[22] = b"<< /ON 23 0 R /OFF 24 0 R /Order 25 0 R >>"
    o[23] = b"[10 0 R 11 0 R]"
    o[24] = b"[]"
    o[25] = b"[10 0 R [11 0 R]]"
    o[32] = b"[30 0 R %s]" % DIRECT_ANNOT
    f["separate_arrays"] = classic(o)
    return f


OPS = ["create", "default_off", "string", "number", "array", "delete",
       "annot_setocg", "annot_setocg_direct", "membership", "membership_direct", "ap_oc"]
# Exact expected sets for a representative selection.
EXPECTED = {
    ("indirect", "default_off"): [20],
    ("direct_in_catalog", "default_off"): [1],
    ("separate_arrays", "default_off"): [23, 24],
    ("objstm", "default_off"): [20],
    ("indirect", "string"): [10],
    ("direct_in_catalog", "number"): [11],
    ("indirect", "annot_setocg_direct"): [3],
    ("separate_arrays", "annot_setocg_direct"): [32],
    ("indirect", "annot_setocg"): [30],
    ("indirect", "membership"): [30],
    ("indirect", "ap_oc"): [30],
    ("indirect", "delete"): [3, 20, 31],
    ("separate_arrays", "delete"): [21, 23, 25, 31, 32],
    ("direct_in_catalog", "delete"): [1, 3, 31],
}
IDS = {"indirect": (1, 20), "direct_in_catalog": (1, 0), "objstm": (1, 20), "separate_arrays": (1, 20)}


def main():
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
    drv = lambda *a: json.loads(subprocess.run([exe, *a], capture_output=True, text=True).stdout.strip() or "{}")

    failures, ran = [], 0

    def check(case, ok, detail=""):
        nonlocal ran
        ran += 1
        print("%-48s %s" % (case, "PASS" if ok else "FAIL " + detail))
        if not ok:
            failures.append(case)

    paths = {}
    for fname, data in fixtures().items():
        paths[fname] = os.path.join(WORK, fname + ".pdf")
        open(paths[fname], "wb").write(data)
        r = drv("ids", paths[fname])
        check("ids %s" % fname, (r.get("catalog"), r.get("ocproperties")) == IDS[fname], json.dumps(r))
        for op in OPS:
            r = drv("op", paths[fname], op)
            mods = r.get("modified", [])
            msgs = []
            if r.get("ret") != 1:
                msgs.append("call failed")
            if not mods or r.get("count") != len(mods):
                msgs.append("no modified objects reported")
            if r.get("listed") != 1:
                msgs.append("listing the report: save/verify %s (mismatch %s)" % (r.get("listed"), r.get("mismatch")))
            bad = [k for k, v in r.get("omit", {}).items() if v == 1]
            if bad:
                msgs.append("omitting %s still verified TRUE (not necessary)" % bad)
            exp = EXPECTED.get((fname, op))
            if exp is not None and mods != exp:
                msgs.append("reported %s, expected %s" % (mods, exp))
            check("%s %s -> %s" % (fname, op, mods), not msgs, "; ".join(msgs) + " " + json.dumps(r))

    # a failed call leaves an empty list
    # (an invalid OCG index; reuse the driver's array op on a document whose
    # OCG 1 exists, so use the 'string' op on a fixture without OCProperties)
    nocp = os.path.join(WORK, "no_ocproperties.pdf")
    o = base(b"<< >>"); del o[31]
    o[30] = b"<< /Type /Annot /Subtype /Square /Rect [300 300 400 400] /F 4 /AP << /N 40 0 R >> >>"
    open(nocp, "wb").write(classic(o))
    r = drv("op", nocp, "string")
    check("failed call -> empty report", r.get("ret") == 0 and r.get("count") == 0, json.dumps(r))

    # verify_empty_stream: empty streams written Flate-encoded verify TRUE
    r = drv("empty", paths["indirect"], "new")
    check("verify: new annotation with an empty /AP stream", r.get("result") == 1, json.dumps(r))
    r = drv("empty", paths["indirect"], "listed", "41")
    check("verify: existing empty stream listed", r.get("result") == 1, json.dumps(r))

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
