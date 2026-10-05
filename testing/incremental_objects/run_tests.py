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
    # The driver dumps a stock incremental save (= every object in the
    # document's map) next to OUT, so tests can check the in-memory state.
    env = dict(os.environ, INCR_DUMP_STOCK=out + ".stock.pdf")
    r = subprocess.run([exe, src, out, "0", RECT, *args], capture_output=True, text=True, env=env)
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
    for objnum, pattern in extra.get("memory_has", []):
        out = res.get("_out")
        dump = open(out + ".stock.pdf", "rb").read() if out and os.path.exists(out + ".stock.pdf") else b""
        m = re.search(rb"(?:^|\n)%d 0 obj\r\n(.*?)endobj" % objnum, dump, re.S)
        if not m or not re.search(pattern, m.group(1)):
            msgs.append("in-memory object %d lacks %r (tolerance not exercised)" % (objnum, pattern))
    if extra.get("nomutate"):
        for sv in res.get("saves", []):
            if sv.get("map_objects", 0) <= 0:
                msgs.append("no map objects counted")
            if sv.get("nomutate_control") != 1:
                msgs.append("stock saves not comparable (control)")
            if sv.get("nomutate") != 1:
                msgs.append("document changed by FPDF_VerifyIncrementalSave")
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
    nums = {"aes_xs": open(os.path.join(FIX, "enc_aes256_xrefstm.nums")).read().strip(),
            "rc4_xs": open(os.path.join(FIX, "enc_rc4_xrefstm.nums")).read().strip()}

    # name, fixture, args, expected status, new objects in the update as
    # offsets from the input /Size (None = not checked), extra checks.
    E = ["--mode", "edit"]
    cases = [
        ("edit_classic", "basic_classic.pdf", E, "ok", [0], {"kind": "table"}),
        ("edit_xrefstm", "basic_xrefstm.pdf", E, "ok", [0], {"kind": "stream"}),
        ("edit_hybrid", "hybrid_xrefstm.pdf", E, "ok", [0], {"kind": "table"}),
        ("edit_aes256_r6", "enc_aes256_r6.pdf", E, "ok", [0], {}),
        ("edit_rc4_r3", "enc_rc4_r3.pdf", E, "ok", [0], {}),
        # M4: CPDF_Page adds /Type /Page in memory; the page is NOT listed
        ("pagetree_page_notype_unlisted", "notype_page_unlisted.pdf", E, "ok", [0],
         {"memory_has": [(3, rb"/Type/Page\b")]}),
        # M4/M5 via CountPages (both documents fix on load)
        ("pagetree_badcount_edit", "badcount_tree.pdf", E, "ok", [0],
         {"memory_has": [(4, rb"/Type/Pages"), (2, rb"/Count 1")]}),
        ("pagetree_notype_edit", "notype_tree.pdf", E, "ok", [0], {}),
        ("pagetree_wrongtype_edit", "wrongtype_tree.pdf", E, "ok", [0], {}),
        # unlisted edit of a direct dictionary inside an unlisted page
        ("direct_child_unlisted", "inline_annot.pdf", ["--mode", "direct_child_unlisted"], "ok", None,
         {"identical": True, "verify": False, "mismatch": 3}),
        # M3 negative: Flate-normalised stream with different data
        ("verify_corrupt_apdata", "basic_classic.pdf", E + ["--corrupt", "apdata"], "ok", [0],
         {"verify": False, "mismatch": 7}),
        # an update must not define an object PDFium never held
        ("verify_injected_object", "basic_classic.pdf", E + ["--corrupt", "inject:6"], "ok", [0],
         {"verify": False, "mismatch": 6}),
        # the original part must be an exact prefix (incl. bytes before %PDF-)
        ("verify_prefix_byte_changed", "basic_classic.pdf", E + ["--corrupt", "origbyte"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_junk_prefix_byte_changed", "junk_prefix.pdf", E + ["--corrupt", "firstbyte"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        # an update that frees (deletes) an object: never loaded (6, content
        # stream) and loaded (3, the edited page)
        ("verify_update_frees_unloaded", "basic_classic.pdf", E + ["--corrupt", "free:6"], "ok", [0],
         {"verify": False, "mismatch": 6}),
        ("verify_update_frees_loaded", "basic_classic.pdf", E + ["--corrupt", "free:3"], "ok", [0],
         {"verify": False, "mismatch": 3}),
        # encrypted originals whose last section is an xref stream
        ("edit_aes256_xrefstm", "enc_aes256_xrefstm.pdf", E, "ok", [0], {"kind": "stream"}),
        ("edit_rc4_xrefstm", "enc_rc4_xrefstm.pdf", E, "ok", [0], {"kind": "stream"}),
        # an xref-stream update freeing a never-loaded object
        ("verify_xs_update_frees_aes256", "enc_aes256_xrefstm.pdf",
         E + ["--corrupt", "xs:free:" + nums["aes_xs"]], "ok", [0],
         {"verify": False, "mismatch": int(nums["aes_xs"])}),
        ("verify_xs_update_frees_rc4", "enc_rc4_xrefstm.pdf",
         E + ["--corrupt", "xs:free:" + nums["rc4_xs"]], "ok", [0],
         {"verify": False, "mismatch": int(nums["rc4_xs"])}),
        ("verify_xs_update_frees_plain", "basic_xrefstm.pdf", E + ["--corrupt", "xs:free:6"], "ok", [0],
         {"verify": False, "mismatch": 6}),
        # malformed xref-stream updates and /XRefStm in an update
        ("verify_xs_odd_index", "basic_xrefstm.pdf", E + ["--corrupt", "xs:oddindex:6"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_xs_trailing_bytes", "basic_xrefstm.pdf", E + ["--corrupt", "xs:trailing:6"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_xs_type_width_5", "basic_xrefstm.pdf", E + ["--corrupt", "xs:wtype5:6"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_update_with_xrefstm", "basic_classic.pdf", E + ["--corrupt", "xrefstm"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        # section-dict keys are name-decoded: an escaped /Prev is followed,
        # an escaped /XRefStm is refused; a /Prev beyond 4 GB (beyond EOF)
        ("verify_escaped_prev_followed", "basic_classic.pdf", E + ["--corrupt", "rep:/Prev=/P#72ev"], "ok", [0], {}),
        ("verify_escaped_xrefstm", "basic_classic.pdf", E + ["--corrupt", "rep:/Prev=/XRef#53tm 0/Prev"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        ("verify_prev_beyond_4gb", "basic_classic.pdf", E + ["--corrupt", "rep:/Prev=/Prev 5000000000/Foo"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        # the update's /Prev skips the original's latest revision
        ("edit_multirev", "multirev_classic.pdf", E, "ok", [0], {}),
        ("verify_prev_skips_revision", "multirev_classic.pdf", E + ["--corrupt", "prevskip"], "ok", [0],
         {"verify": False, "mismatch": 0}),
        # an update entry pointing a never-loaded object at original bytes
        ("verify_update_remaps_into_original", "basic_classic.pdf", E + ["--corrupt", "remap:6"], "ok", [0],
         {"verify": False, "mismatch": 6}),
        # /Info changed in the update (no public API edits /Info in memory)
        ("verify_tampered_info", "with_info.pdf", E + ["--load-info", "--corrupt", "inject:5"], "ok", [0],
         {"verify": False, "mismatch": 5}),
        # a real page change that was not listed: verify must report page 3
        ("pagetree_rotate_unlisted", "notype_tree.pdf", ["--mode", "rotate_unlisted"], "ok", None,
         {"identical": True, "verify": False, "mismatch": 3}),
        # M7: form loaded (/FT /Ff copied to field 8, /T of 11 made direct)
        ("form_fixes_edit", "acroform_fixes.pdf", ["--mode", "form_edit"], "ok", [0],
         {"memory_has": [(8, rb"/FT/Tx"), (8, rb"/Ff 4096"), (11, rb"/T\(Second\)")]}),
        # creating the form page view regenerates appearances of widgets
        # without /AP (new streams referenced from unlisted widgets): refused
        ("form_ap_regeneration_refused", "acroform_noap.pdf", ["--mode", "form_edit"], "refused", None, {}),
        # M7 negative: parent has /FT, so nothing is copied; an unlisted /Ff
        # on the parent equal to the kid's must still fail
        ("form_parent_flags_unlisted", "acroform_parent_ft.pdf", ["--mode", "form_flags_unlisted"], "ok", [0],
         {"verify": False, "mismatch": 8, "memory_has": [(8, rb"/Ff 4096")]}),
        # field value changed but not listed: verify must report field 11
        ("form_value_unlisted", "acroform_fixes.pdf", ["--mode", "form_value_unlisted"], "ok", [0],
         {"verify": False, "mismatch": 11}),
        # verify does not change the open document (map and objects)
        ("verify_no_mutation_classic", "basic_classic.pdf", ["--mode", "nomutate"], "ok", [0], {"nomutate": True}),
        ("verify_no_mutation_xrefstm", "basic_xrefstm.pdf", ["--mode", "nomutate"], "ok", [0], {"nomutate": True}),
        # (RC4, not AES: AES uses random IVs, so two saves never match.)
        ("verify_no_mutation_rc4", "enc_rc4_r3.pdf", ["--mode", "nomutate"], "ok", [0], {"nomutate": True}),
        ("verify_no_mutation_form", "acroform_fixes.pdf", ["--mode", "nomutate"], "ok", [0], {"nomutate": True}),
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
        res["_out"] = out
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
