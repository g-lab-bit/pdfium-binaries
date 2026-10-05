#!/usr/bin/env python3
"""Scripted test for FPDF_SaveIncrementalObjects (incremental_objects.patch).

usage: run_tests.py PDFIUM_DIR --qpdf PATH [--work DIR]
PDFIUM_DIR = an unpacked pdfium-binaries artifact (include/, lib/). macOS/Linux
(cc). Requires pikepdf and qpdf; the run fails immediately without them.
Builds incr_driver.c against the artifact, generates fixtures, runs every case
and checks each result with check_update.py (independent parser) and
`qpdf --check`. Every case must run: a missing fixture or a case that did not
produce a result counts as a failure. Exit status 0 = all cases passed.

This replaces PDFium embedder tests: CI only builds the library (the
pdfium_embeddertests target would need workflow/step changes and the test
data checkout), so the cases run against the CI artifact instead.
"""
import json, os, platform, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_update  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out")
QPDF = sys.argv[sys.argv.index("--qpdf") + 1] if "--qpdf" in sys.argv else shutil.which("qpdf")
if not QPDF or not os.path.exists(QPDF):
    sys.exit("run_tests.py: qpdf is required (--qpdf PATH)")
try:
    import pikepdf  # noqa: F401
except ImportError:
    sys.exit("run_tests.py: pikepdf is required (pip install pikepdf)")
FIX = os.path.join(WORK, "fixtures")
OUTDIR = os.path.join(WORK, "outputs")
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
    src = os.path.join(FIX, fixture)
    if not os.path.exists(src):
        return None, {"status": "missing fixture"}
    r = subprocess.run([exe, src, out, "0", RECT, *args], capture_output=True, text=True)
    res = next((json.loads(l[7:]) for l in r.stdout.splitlines() if l.startswith("RESULT ")), {})
    return r.returncode, res


def check(orig, saved):
    import io, contextlib
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["check_update.py", orig, saved]
    try:
        with contextlib.redirect_stdout(buf):
            check_update.main()
    finally:
        sys.argv = old
    return json.loads(buf.getvalue())


def qpdf_ok(path):
    r = subprocess.run([QPDF, "--check", "--password=", path], capture_output=True, text=True)
    return r.returncode in (0, 3)  # 3 = warnings only


def verify_checks(res, extra):
    """FPDF_VerifyIncrementalSave result of every save of a case."""
    msgs = []
    want = extra.get("verify", True)
    for i, sv in enumerate(res.get("saves", [])):
        if sv.get("verify") is not want:
            msgs.append("save %d: verify=%s, expected %s (mismatch %s)"
                        % (i, sv.get("verify"), want, sv.get("mismatch")))
        elif not want:
            exp = sv["objnums"][0] if extra.get("mismatch") == "holder" else extra.get("mismatch")
            if sv.get("mismatch") != exp:
                msgs.append("first_mismatch %s, expected %s" % (sv.get("mismatch"), exp))
    return msgs


def verify_ok(fixture, out, res, new, extra):
    """Checks for a successful save; returns a list of failure messages."""
    msgs = []
    c = check(os.path.join(FIX, fixture), out)
    if not c["prefix"]:
        return ["original is not a byte prefix"]
    if extra.get("identical"):
        return [] if c.get("identical") else ["noop output differs from original"]
    secs = c.get("sections", [])
    if extra.get("sections", 1) != len(secs):
        msgs.append("sections %d" % len(secs))
    for k in ("entries_ok", "prev_eq_orig_startxref", "size_ge_orig", "encrypt_same"):
        if c.get(k) is not True:
            msgs.append("%s=%s" % (k, c.get(k)))
    if c.get("id0_same") is not True:
        msgs.append("id0_same=%s" % c.get("id0_same"))
    if extra.get("kind") and secs and secs[0]["kind"] != extra["kind"]:
        msgs.append("kind %s" % secs[0]["kind"])
    saves = res.get("saves", [])
    input_size = c.get("orig_size")
    for i, s in enumerate(secs):
        if not s["entries_cover_objs"]:
            msgs.append("section %d: object without xref entry" % i)
        if s["kind"] == "stream" and not (s["has_type_xref"] and s["self_entry"] and s["data_len_exact"]):
            msgs.append("section %d: bad xref stream" % i)
        if new is not None and i < len(saves) and input_size is not None:
            expect = set(saves[i]["objnums"]) | {input_size + o for o in new}
            got = {o for o in s["objs"] if o != s.get("self_num")}
            if got != expect:
                msgs.append("section %d objects %s, expected %s" % (i, sorted(got), sorted(expect)))
        input_size = s["size"]
    if extra.get("direct_length"):
        tail = open(out, "rb").read()[os.path.getsize(os.path.join(FIX, fixture)):]
        if not re.search(rb"/Length \d+>>stream", tail):
            msgs.append("stream /Length not written as a direct number")
    if not qpdf_ok(out):
        msgs.append("qpdf --check failed")
    return msgs


def main():
    exe = build()
    subprocess.run([sys.executable, os.path.join(HERE, "make_fixtures.py"), FIX, "--qpdf", QPDF], check=True)
    os.makedirs(OUTDIR, exist_ok=True)
    meta, enc = open(os.path.join(FIX, "enc_aes128_metadata.nums")).read().split()

    # name, fixture, args, expected status, new objects in the update as
    # offsets from the input /Size (None = not checked), extra checks.
    E = ["--mode", "edit"]
    cases = [
        ("edit_classic", "basic_classic.pdf", E, "ok", [0], {"kind": "table"}),
        ("edit_xrefstm", "basic_xrefstm.pdf", E, "ok", [0], {"kind": "stream"}),
        ("edit_hybrid", "hybrid_xrefstm.pdf", E, "ok", [0], {"kind": "table"}),
        ("edit_aes256_r6", "enc_aes256_r6.pdf", E, "ok", [0], {}),
        ("edit_rc4_r3", "enc_rc4_r3.pdf", E, "ok", [0], {}),
        # FPDF_VerifyIncrementalSave on a corrupted copy of the saved file
        ("verify_corrupt_value_classic", "basic_classic.pdf", E + ["--corrupt", "nm"], "ok", [0],
         {"verify": False, "mismatch": "holder"}),
        ("verify_corrupt_value_xrefstm", "basic_xrefstm.pdf", E + ["--corrupt", "nm"], "ok", [0],
         {"verify": False, "mismatch": "holder"}),
        ("verify_corrupt_startxref", "basic_classic.pdf", E + ["--corrupt", "startxref"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_corrupt_value_aes256", "enc_aes256_r6.pdf", E + ["--corrupt", "nm"], "ok", [0],
         {"verify": False, "mismatch": "holder"}),
        ("edit_aes128_metadata", "enc_aes128_metadata.pdf", E, "ok", [0], {}),
        ("repeat3_xrefstm", "basic_xrefstm.pdf", E + ["--repeat", "3"], "ok", [0], {"sections": 3}),
        ("repeat3_classic", "basic_classic.pdf", E + ["--repeat", "3"], "ok", [0], {"sections": 3}),
        ("touch_classic", "basic_classic.pdf", ["--mode", "touch"], "ok", [], {}),
        ("touch_hybrid", "hybrid_xrefstm.pdf", ["--mode", "touch"], "ok", [], {}),
        ("noop_classic", "basic_classic.pdf", ["--mode", "noop"], "ok", None, {"identical": True}),
        ("render_then_noop", "basic_classic.pdf", ["--mode", "noop", "--render"], "ok", None, {"identical": True}),
        ("render_then_edit", "basic_classic.pdf", E + ["--render"], "ok", [0], {}),
        # the removed annotation's /AP took Size+0 and must not be written
        ("removed_annot_not_written", "basic_classic.pdf", ["--mode", "removed"], "ok", [1], {}),
        # first /AP (Size+0) orphaned by a second FPDFAnnot_SetAP
        ("ap_replaced_orphan_not_written", "basic_classic.pdf", ["--mode", "apreplaced"], "ok", [1], {}),
        # chromium/8076 makes the stock font a direct dict: nothing new
        ("unused_stdfont_not_written", "basic_classic.pdf", ["--mode", "stdfont"], "ok", [0], {}),
        ("direct_kid_refused", "direct_kid.pdf", E, "refused", None, {}),
        # control: listing /Pages too (its /Kids references the page PDFium
        # made indirect, = Size+0) makes the edit valid; /AP = Size+1
        ("direct_kid_with_pages_ok", "direct_kid.pdf", E + ["--list", "2"], "ok", [0, 1], {}),
        ("gen1_page_edit", "gen1_page.pdf", E, "refused", None, {}),
        ("gen1_page_list", "gen1_page.pdf", ["--mode", "list", "--list", "3"], "refused", None, {}),
        ("gen1_ref_edit", "gen1_ref.pdf", E, "refused", None, {}),
        ("gen1_ref_touch", "gen1_ref.pdf", ["--mode", "touch"], "refused", None, {}),
        ("gen1_unrelated_edit", "gen1_unrelated.pdf", E, "ok", [0], {}),
        # PDFium makes every stream's /Length direct on load, so an indirect
        # gen-1 /Length object is neither referenced nor written.
        ("gen1_length_unfiltered_ok", "gen1_length_unfiltered.pdf", ["--mode", "list", "--list", "4"], "ok", [], {"direct_length": True}),
        ("gen1_length_filtered_ok", "gen1_length_filtered.pdf", ["--mode", "list", "--list", "4"], "ok", [], {"direct_length": True}),
        ("damaged_classic_edit", "damaged_classic.pdf", E, "refused", None, {}),
        ("damaged_classic_noop", "damaged_classic.pdf", ["--mode", "noop"], "refused", None, {}),
        ("damaged_xrefstm_edit", "damaged_xrefstm.pdf", E, "refused", None, {}),
        ("nonexistent_objnum", "basic_classic.pdf", ["--mode", "list", "--list", "9999"], "refused", None, {}),
        ("objnum_zero", "basic_classic.pdf", ["--mode", "list", "--list", "0"], "refused", None, {}),
        ("free_objnum", "free_entry.pdf", ["--mode", "list", "--list", "5"], "refused", None, {}),
        ("next_to_free", "free_entry.pdf", ["--mode", "list", "--list", "6"], "ok", [], {}),
        ("huge_size_refused", "huge_size.pdf", E, "refused", None, {}),
        ("newdoc_refused", "basic_classic.pdf", ["--mode", "newdoc"], "refused", None, {}),
        ("no_final_eol", "no_final_eol.pdf", E, "ok", [0], {}),
        ("junk_prefix", "junk_prefix.pdf", E, "ok", [0], {}),
        ("avail_nonlinearized_edit", "basic_classic.pdf", E + ["--avail"], "ok", [0], {}),
        ("enc_metadata_listed", "enc_aes128_metadata.pdf", ["--mode", "list", "--list", meta], "refused", None, {}),
        ("enc_encrypt_listed", "enc_aes128_metadata.pdf", ["--mode", "list", "--list", enc], "refused", None, {}),
        ("linearized_edit", "linearized.pdf", E, "ok", [0], {}),
        ("linearized_avail_refused", "linearized.pdf", E + ["--avail"], "refused", None, {}),
    ]

    failures, ran, planned_wf = [], 0, 0

    def report(name, status, msgs):
        nonlocal ran
        ran += 1
        print("%-34s %-16s %s" % (name, status, "PASS" if not msgs else "FAIL: " + "; ".join(msgs)))
        if msgs:
            failures.append(name)

    for name, fx, args, expect, new, extra in cases:
        out = os.path.join(OUTDIR, name + ".pdf")
        if os.path.exists(out):
            os.unlink(out)
        rc, res = run(exe, fx, out, *args)
        status = res.get("status", "no result (rc=%s)" % rc)
        msgs = [] if status == expect else ["status %s != %s" % (status, expect)]
        if status == "refused":
            if res["saves"][-1].get("bytes_written", -1) != 0:
                msgs.append("refusal wrote %s bytes" % res["saves"][-1].get("bytes_written"))
            if os.path.exists(out):
                msgs.append("output written on refusal")
        if status == "ok" == expect:
            msgs += verify_ok(fx, out, res, new, extra)
            msgs += verify_checks(res, extra)
        report(name, status, msgs)

    # Writer failures: the FPDF_FILEWRITE fails once the output would pass N
    # bytes, at several N (small file: only the final flush writes).
    for fx in ("basic_classic.pdf", "big_classic.pdf"):
        good = os.path.join(OUTDIR, "wf_ok_" + fx)
        rc, res = run(exe, fx, good, *E)
        if res.get("status") != "ok":
            planned_wf += 1
            report("writefail_" + fx, res.get("status", "?"), ["baseline save failed"])
            continue
        total, orig = os.path.getsize(good), os.path.getsize(os.path.join(FIX, fx))
        points = sorted(n for n in {0, 1000, 40000, orig - 1, orig + 10, total - 1} if n < total)
        planned_wf += len(points) + 1
        for n in points:
            out = os.path.join(OUTDIR, "wf_%d_%s" % (n, fx))
            rc, res = run(exe, fx, out, *E, "--fail-after", str(n))
            st = res.get("status", "no result")
            msgs = [] if st == "refused" and rc == 2 else ["expected FALSE, got %s" % st]
            report("writefail_%s_after_%d" % (fx[:-4], n), st, msgs)
        rc, res = run(exe, fx, os.path.join(OUTDIR, "wf_exact_" + fx), *E, "--fail-after", str(total))
        report("writefail_%s_exact_total_ok" % fx[:-4], res.get("status", "?"),
               [] if res.get("status") == "ok" else ["save with exactly enough room failed"])

    # One open document, several saves (no reopen).
    base = os.path.join(OUTDIR, "session")
    for suf in (".s1.pdf", ".s2.pdf", ".s2_noL3.pdf"):
        if os.path.exists(base + suf):
            os.unlink(base + suf)
    rc, res = run(exe, "basic_classic.pdf", base, "--mode", "session")
    rd = lambda p: open(p, "rb").read() if os.path.exists(p) else b""
    s2, nol3 = rd(base + ".s2.pdf"), rd(base + ".s2_noL3.pdf")
    rot = lambda d: re.search(rb"/Rotate\s+90", d) is not None
    msgs = []
    if not (res.get("s1") == 1 and res.get("s2_cumulative") == 1):
        msgs.append("cumulative saves failed")
    if not (b"session-p0" in s2 and b"session-p1" in s2 and rot(s2)):
        msgs.append("cumulative save lacks an edit")
    if res.get("s2_only_L2L3") != 0 or res.get("s2_only_bytes") != 0:
        msgs.append("L2+L3-only save was not refused before writing")
    if not (res.get("s2_noL3") == 1 and b"session-p0" in nol3 and not rot(nol3)):
        msgs.append("unlisted rotation was expected to be lost by the save")
    if not (res.get("verify_s1") == 1 and res.get("verify_s2") == 1):
        msgs.append("verify of a complete save was not TRUE")
    if not (res.get("verify_s2_noL3") == 0 and res.get("mismatch_s2_noL3") == res.get("L", [0, 0, 0])[2]):
        msgs.append("verify did not catch the unlisted rotation at the page (got %s/%s)"
                    % (res.get("verify_s2_noL3"), res.get("mismatch_s2_noL3")))
    for p in (base + ".s1.pdf", base + ".s2.pdf"):
        c = check(os.path.join(FIX, "basic_classic.pdf"), p) if os.path.exists(p) else {}
        if not (c.get("prefix") and c.get("entries_ok") and c.get("id0_same") and qpdf_ok(p)):
            msgs.append("bad structure " + os.path.basename(p))
    report("session_one_handle", res.get("status", "no result"), msgs)
    print("SESSION " + json.dumps(res))

    expected_count = len(cases) + 1 + planned_wf
    print("\n%d case(s) run, %d failure(s)%s" % (ran, len(failures), (": " + ", ".join(failures)) if failures else ""))
    if ran < expected_count:
        print("FAIL: fewer cases ran than defined")
        sys.exit(1)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
