#!/usr/bin/env python3
"""Scripted test for filter_unchanged.patch (FPDF_FilterUnchangedObjects).

usage: run_tests.py PDFIUM_DIR --qpdf PATH [--work DIR]
Requires qpdf (encrypted + object-stream fixture) and pikepdf (object
numbers). Every case must run; exit status 0 = all passed.
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
QPDF = sys.argv[sys.argv.index("--qpdf") + 1] if "--qpdf" in sys.argv else shutil.which("qpdf")
if not QPDF or not os.path.exists(QPDF):
    sys.exit("run_tests.py: qpdf is required (--qpdf PATH)")


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
    out += b"trailer\n<< /Size %d /Root %d 0 R /ID [<00112233445566778899aabbccddeeff><00112233445566778899aabbccddeeff>] >>\nstartxref\n%d\n%%%%EOF\n" % (size, root, x)
    return bytes(out)


CONTENT = b"0 0 1 rg 50 50 100 100 re f\n"
FIXTURE = {
    1: b"<< /Type /Catalog /Pages 2 0 R >>",
    2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> /Annots [10 0 R] >>",
    4: b"<< /Length %d >>\nstream\n" % len(CONTENT) + CONTENT + b"endstream",
    10: b"<< /Type /Annot /Subtype /Square /Rect [100.5 200 300 400.25] /C [1 0 0] /CA 1 /F 4 >>",
}


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

    plain = os.path.join(WORK, "plain.pdf")
    open(plain, "wb").write(classic(FIXTURE))
    enc = os.path.join(WORK, "aes256_objstm.pdf")
    subprocess.run([QPDF, "--object-streams=generate", "--encrypt", "", "owner", "256", "--",
                    plain, enc], check=True)
    rc4 = os.path.join(WORK, "rc4_objstm.pdf")
    subprocess.run([QPDF, "--allow-weak-crypto", "--object-streams=generate", "--encrypt", "", "owner",
                    "128", "--use-aes=n", "--", plain, rc4], check=True)

    failures, ran = [], 0

    def check(case, ok, detail=""):
        nonlocal ran
        ran += 1
        print("%-52s %s" % (case, "PASS" if ok else "FAIL " + detail))
        if not ok:
            failures.append(case)

    # Non-mutation is checked with byte snapshots of stock saves, which are
    # only reproducible without AES (random IVs): checked on plain and RC4.
    for label, path, snapshots in (("plain", plain, True), ("RC4 + ObjStm", rc4, True),
                                   ("AES-256 + ObjStm", enc, False)):
        pdf = pikepdf.open(path)
        annot = pdf.pages[0].obj.Annots[0].objgen[0]
        page = pdf.pages[0].obj.objgen[0]
        content = pdf.pages[0].obj.Contents.objgen[0]
        if label != "plain":  # the encrypted copies use object streams
            xref = {o.objgen[0] for o in pdf.objects if o.is_indirect}
            check("%s: annotation is inside an object stream" % label,
                  annot in xref and b"/ObjStm" in open(path, "rb").read())
        common = lambda r: (not snapshots or (r.get("map_unchanged") == 1 and r.get("objects_unchanged") == 1)) \
            and r.get("saved") == 1 and r.get("verify") == 1
        r = drv("revert", path)
        check("%s: edit + revert -> filtered out, save identical" % label,
              r.get("edit") == 1 and r.get("before") == [annot, page] and r.get("after") == []
              and common(r) and r.get("identical_to_original") == 1, json.dumps(r))
        r = drv("change", path)
        check("%s: real change -> annotation kept, page dropped" % label,
              r.get("edit") == 1 and r.get("after") == [annot] and common(r)
              and r.get("identical_to_original") == 0, json.dumps(r))
        r = drv("unloaded", path, str(content))
        check("%s: never-loaded object -> filtered out" % label,
              r.get("after") == [] and common(r) and r.get("identical_to_original") == 1, json.dumps(r))
        r = drv("newobj", path)
        new_obj = r.get("before", [0])[0]
        check("%s: new object -> kept" % label,
              r.get("edit") == 1 and new_obj in r.get("after", []) and common(r), json.dumps(r))

    print("\n%d case(s), %d failure(s)" % (ran, len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
