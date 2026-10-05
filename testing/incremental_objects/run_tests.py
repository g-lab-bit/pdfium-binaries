#!/usr/bin/env python3
"""Scripted test for FPDF_SaveIncrementalObjects (incremental_objects.patch).

usage: run_tests.py PDFIUM_DIR [--work DIR] [--qpdf PATH]
PDFIUM_DIR = an unpacked pdfium-binaries artifact (include/, lib/). macOS/Linux
(clang/cc). Builds incr_driver.c against it, generates fixtures, runs every case
and checks the result with check_update.py (independent parser) and, when
available, `qpdf --check`. Exit status 0 = all cases passed.

This replaces PDFium embedder tests: CI only builds the library (the
pdfium_embeddertests target would need workflow/step changes and the test
data checkout), so the cases are run against the CI artifact instead.
"""
import json, os, platform, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_update  # noqa: E402

PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
QPDF = sys.argv[sys.argv.index("--qpdf") + 1] if "--qpdf" in sys.argv else shutil.which("qpdf")
FIX = os.path.join(WORK, "fixtures")
RECT = "100,100,150,150"


def build():
    os.makedirs(WORK, exist_ok=True)
    lib = os.path.join(WORK, "lib")
    os.makedirs(lib, exist_ok=True)
    name = "libpdfium.dylib" if platform.system() == "Darwin" else "libpdfium.so"
    shutil.copy(os.path.join(PD, "lib", name), lib)
    if platform.system() == "Darwin":
        subprocess.run(["install_name_tool", "-id", "@rpath/" + name, os.path.join(lib, name)],
                       check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["codesign", "-f", "-s", "-", os.path.join(lib, name)],
                       stderr=subprocess.DEVNULL)
    exe = os.path.join(WORK, "incr_driver")
    subprocess.run(["cc", "-O1", "-I", os.path.join(PD, "include"), os.path.join(HERE, "incr_driver.c"),
                    "-L", lib, "-lpdfium", "-Wl,-rpath," + lib, "-o", exe], check=True)
    return exe


def run(exe, fixture, out, *args):
    r = subprocess.run([exe, os.path.join(FIX, fixture), out, "0", RECT, *args],
                       capture_output=True, text=True)
    res = next((json.loads(l[7:]) for l in r.stdout.splitlines() if l.startswith("RESULT ")), {})
    return r.returncode, res


def check(orig, saved):
    import io, contextlib
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["check_update.py", orig, saved]
    with contextlib.redirect_stdout(buf):
        check_update.main()
    sys.argv = old
    return json.loads(buf.getvalue())


def qpdf_ok(path):
    if not QPDF:
        return None
    r = subprocess.run([QPDF, "--check", "--password=", path], capture_output=True, text=True)
    return r.returncode in (0, 3)  # 3 = warnings


def main():
    exe = build()
    subprocess.run([sys.executable, os.path.join(HERE, "make_fixtures.py"), FIX] +
                   (["--qpdf", QPDF] if QPDF else []), check=True)
    outdir = os.path.join(WORK, "outputs")
    os.makedirs(outdir, exist_ok=True)
    nums = {}
    if os.path.exists(os.path.join(FIX, "enc_aes128_metadata.nums")):
        meta, enc = open(os.path.join(FIX, "enc_aes128_metadata.nums")).read().split()
        nums = {"META": meta, "ENC": enc}

    # name, fixture, args, expected status, expected objects in the update
    # (excluding the xref stream object), extra checks
    cases = [
        ("edit_classic", "basic_classic.pdf", ["--mode", "edit"], "ok", 2, {"kind": "table"}),
        ("edit_xrefstm", "basic_xrefstm.pdf", ["--mode", "edit"], "ok", 2, {"kind": "stream"}),
        ("repeat3_xrefstm", "basic_xrefstm.pdf", ["--mode", "edit", "--repeat", "3"], "ok", None, {"sections": 3}),
        ("touch_classic", "basic_classic.pdf", ["--mode", "touch"], "ok", 1, {}),
        ("noop_classic", "basic_classic.pdf", ["--mode", "noop"], "ok", 0, {"identical": True}),
        ("render_then_noop", "basic_classic.pdf", ["--mode", "noop", "--render"], "ok", 0, {"identical": True}),
        ("render_then_edit", "basic_classic.pdf", ["--mode", "edit", "--render"], "ok", 2, {}),
        ("removed_annot_not_written", "basic_classic.pdf", ["--mode", "removed"], "ok", 2, {}),
        ("unused_stdfont_not_written", "basic_classic.pdf", ["--mode", "stdfont"], "ok", 2, {}),
        ("ap_replaced_orphan_not_written", "basic_classic.pdf", ["--mode", "apreplaced"], "ok", 2, {}),
        ("direct_kid_refused", "direct_kid.pdf", ["--mode", "edit"], "refused", None, {}),
        # control: listing the /Pages node too (its /Kids now references the
        # page PDFium made indirect) makes the same edit valid.
        ("direct_kid_with_pages_ok", "direct_kid.pdf", ["--mode", "edit", "--list", "2"], "ok", 3, {}),
        ("gen1_page_edit", "gen1_page.pdf", ["--mode", "edit"], "refused", None, {}),
        ("gen1_page_list", "gen1_page.pdf", ["--mode", "list", "--list", "3"], "refused", None, {}),
        ("gen1_ref_edit", "gen1_ref.pdf", ["--mode", "edit"], "refused", None, {}),
        ("gen1_ref_touch", "gen1_ref.pdf", ["--mode", "touch"], "refused", None, {}),
        ("gen1_unrelated_edit", "gen1_unrelated.pdf", ["--mode", "edit"], "ok", 2, {}),
        ("damaged_classic_edit", "damaged_classic.pdf", ["--mode", "edit"], "refused", None, {}),
        ("damaged_classic_noop", "damaged_classic.pdf", ["--mode", "noop"], "refused", None, {}),
        ("damaged_xrefstm_edit", "damaged_xrefstm.pdf", ["--mode", "edit"], "refused", None, {}),
        ("nonexistent_objnum", "basic_classic.pdf", ["--mode", "list", "--list", "9999"], "refused", None, {}),
        ("objnum_zero", "basic_classic.pdf", ["--mode", "list", "--list", "0"], "refused", None, {}),
        ("free_objnum", "free_entry.pdf", ["--mode", "list", "--list", "5"], "refused", None, {}),
        ("next_to_free", "free_entry.pdf", ["--mode", "list", "--list", "6"], "ok", 1, {}),
        ("huge_size_refused", "huge_size.pdf", ["--mode", "edit"], "refused", None, {}),
        ("newdoc_refused", "basic_classic.pdf", ["--mode", "newdoc"], "refused", None, {}),
        ("no_final_eol", "no_final_eol.pdf", ["--mode", "edit"], "ok", 2, {}),
        ("junk_prefix", "junk_prefix.pdf", ["--mode", "edit"], "ok", 2, {}),
        ("avail_nonlinearized_edit", "basic_classic.pdf", ["--mode", "edit", "--avail"], "ok", 2, {}),
    ]
    if nums:
        cases += [
            ("enc_metadata_listed", "enc_aes128_metadata.pdf", ["--mode", "list", "--list", nums["META"]], "refused", None, {}),
            ("enc_encrypt_listed", "enc_aes128_metadata.pdf", ["--mode", "list", "--list", nums["ENC"]], "refused", None, {}),
            ("enc_edit", "enc_aes128_metadata.pdf", ["--mode", "edit"], "ok", 2, {}),
        ]
    if os.path.exists(os.path.join(FIX, "linearized.pdf")):
        cases += [
            ("linearized_edit", "linearized.pdf", ["--mode", "edit"], "ok", 2, {}),
            ("linearized_avail_refused", "linearized.pdf", ["--mode", "edit", "--avail"], "refused", None, {}),
        ]

    failures = []
    for name, fx, args, expect, nobjs, extra in cases:
        out = os.path.join(outdir, name + ".pdf")
        if os.path.exists(out):
            os.unlink(out)
        rc, res = run(exe, fx, out, *args)
        status = res.get("status", "rc=%d" % rc)
        msgs = []
        if status != expect:
            msgs.append("status %s != %s" % (status, expect))
        if expect == "refused":
            saves = res.get("saves", [{}])
            if saves and saves[-1].get("bytes_written", 0) != 0:
                msgs.append("refusal wrote %s bytes" % saves[-1].get("bytes_written"))
        if expect == "ok" and status == "ok":
            c = check(os.path.join(FIX, fx), out)
            if not c["prefix"]:
                msgs.append("original is not a prefix")
            if extra.get("identical"):
                if not c.get("identical"):
                    msgs.append("noop output differs from original")
            else:
                secs = c.get("sections", [])
                if not c.get("entries_ok") or not c.get("prev_eq_orig_startxref") or not c.get("size_ge_orig"):
                    msgs.append("xref/trailer check failed: %s" % json.dumps(c)[:300])
                if extra.get("sections") and len(secs) != extra["sections"]:
                    msgs.append("sections %d" % len(secs))
                if extra.get("kind") and secs and secs[0]["kind"] != extra["kind"]:
                    msgs.append("kind %s" % secs[0]["kind"])
                if nobjs is not None and secs:
                    objs = [o for o in secs[0]["objs"] if o != secs[0].get("self_num")]
                    if len(objs) != nobjs:
                        msgs.append("update objects %s, expected %d" % (objs, nobjs))
                for s in secs:
                    if s["kind"] == "stream" and not (s["has_type_xref"] and s["self_entry"] and s["data_len_exact"]):
                        msgs.append("bad xref stream")
            q = qpdf_ok(out)
            if q is False:
                msgs.append("qpdf --check failed")
        print("%-28s %-8s %s" % (name, status, "PASS" if not msgs else "FAIL: " + "; ".join(msgs)))
        if msgs:
            failures.append(name)

    # One open document, several saves (no reopen).
    base = os.path.join(outdir, "session")
    rc, res = run(exe, "basic_classic.pdf", base, "--mode", "session")
    s2 = open(base + ".s2.pdf", "rb").read() if os.path.exists(base + ".s2.pdf") else b""
    nol3 = open(base + ".s2_noL3.pdf", "rb").read() if os.path.exists(base + ".s2_noL3.pdf") else b""
    rot = lambda d: re.search(rb"/Rotate\s+90", d) is not None
    msgs = []
    if not (res.get("s1") == 1 and res.get("s2_cumulative") == 1):
        msgs.append("cumulative saves failed")
    if not (b"session-p0" in s2 and b"session-p1" in s2 and rot(s2)):
        msgs.append("cumulative save lacks an edit")
    if res.get("s2_only_L2L3") != 0 or res.get("s2_only_bytes") != 0:
        msgs.append("L2+L3-only save was not refused before writing")
    if not (res.get("s2_noL3") == 1 and b"session-p0" in nol3 and not rot(nol3)):
        msgs.append("unlisted rotation was expected to be silently lost")
    for p in (base + ".s1.pdf", base + ".s2.pdf"):
        c = check(os.path.join(FIX, "basic_classic.pdf"), p) if os.path.exists(p) else {}
        if not (c.get("prefix") and c.get("entries_ok")):
            msgs.append("bad structure " + os.path.basename(p))
    print("%-28s %-8s %s" % ("session_one_handle", "", "PASS" if not msgs else "FAIL: " + "; ".join(msgs)))
    print("SESSION " + json.dumps(res))
    if msgs:
        failures.append("session_one_handle")

    print("\n%d failure(s)%s" % (len(failures), (": " + ", ".join(failures)) if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
