#!/usr/bin/env python3
"""Corpus run for FPDF_SaveIncrementalObjects + FPDF_VerifyIncrementalSave.

usage: run_corpus.py PDFIUM_DIR --corpus DIR [--work DIR]
For every DIR/*.pdf and every mode (edit, repeat x3, touch, noop, and edit
after rendering every page) the driver
saves on page 0 and verifies each save on the same open document. Expected:
files whose name starts with "damaged_" are refused (rebuilt xref); every
other file saves and every verify is TRUE. Prints a timing table. The corpus
is required (the Rapida save-spike corpus is not redistributable, so it is
not in this repository); the run fails if it is missing or empty.
"""
import glob, json, os, platform, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2 or "--corpus" not in sys.argv:
    sys.exit(__doc__)
PD = os.path.abspath(sys.argv[1])
CORPUS = os.path.abspath(sys.argv[sys.argv.index("--corpus") + 1])
WORK = os.path.abspath(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else os.path.join(HERE, "out_corpus")
FILES = sorted(glob.glob(os.path.join(CORPUS, "*.pdf")))
if not FILES:
    sys.exit("run_corpus.py: no PDFs in %s" % CORPUS)
MODES = {"edit": ["--mode", "edit"], "repeat3": ["--mode", "edit", "--repeat", "3"],
         "touch": ["--mode", "touch"], "noop": ["--mode", "noop"],
         # worst case for verify: every page rendered (content streams,
         # fonts, images loaded into the document) before the edit
         "edit_render_all": ["--mode", "edit", "--render-all"]}


def build():
    lib = os.path.join(WORK, "lib")
    os.makedirs(lib, exist_ok=True)
    name = "libpdfium.dylib" if platform.system() == "Darwin" else "libpdfium.so"
    shutil.copy(os.path.join(PD, "lib", name), lib)
    if platform.system() == "Darwin":
        subprocess.run(["install_name_tool", "-id", "@rpath/" + name, os.path.join(lib, name)],
                       check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["codesign", "-f", "-s", "-", os.path.join(lib, name)], stderr=subprocess.DEVNULL)
    exe = os.path.join(WORK, "incr_driver")
    subprocess.run(["cc", "-O1", "-I", os.path.join(PD, "include"), os.path.join(HERE, "incr_driver.c"),
                    "-L", lib, "-lpdfium", "-Wl,-rpath," + lib, "-o", exe], check=True)
    return exe


def main():
    os.makedirs(WORK, exist_ok=True)
    exe = build()
    failures, timing = [], []
    for f in FILES:
        base = os.path.basename(f)
        damaged = base.startswith("damaged_")
        for mode, args in MODES.items():
            out = os.path.join(WORK, "%s.%s.pdf" % (base, mode))
            r = subprocess.run([exe, f, out, "0", "100,100,150,150", *args], capture_output=True, text=True)
            res = next((json.loads(l[7:]) for l in r.stdout.splitlines() if l.startswith("RESULT ")), {})
            st, saves = res.get("status"), res.get("saves", [])
            msg = []
            if damaged:
                if st != "refused":
                    msg.append("expected refusal, got %s" % st)
            else:
                if st != "ok":
                    msg.append("status %s" % st)
                for i, sv in enumerate(saves):
                    if sv.get("verify") is not True:
                        msg.append("save %d verify FALSE (mismatch %s)" % (i, sv.get("mismatch")))
                if len(saves) != (3 if mode == "repeat3" else 1):
                    msg.append("%d saves" % len(saves))
            print("%-36s %-8s %-8s %s" % (base, mode, st, "PASS" if not msg else "FAIL: " + "; ".join(msg)))
            if msg:
                failures.append((base, mode))
            if not damaged and saves:
                timing.append((base, mode, os.path.getsize(f) / 1e6,
                               sum(s.get("save_ms", 0) for s in saves),
                               sum(s.get("verify_ms", 0) for s in saves), len(saves)))
    print("\n| file | mode | MB | save ms (total) | verify ms (total) | saves |")
    print("|---|---|---|---|---|---|")
    for b, m, mb, sm, vm, n in timing:
        print("| %s | %s | %.1f | %.1f | %.1f | %d |" % (b, m, mb, sm, vm, n))
    print("\n%d file x mode runs, %d failure(s)%s" % (len(FILES) * len(MODES), len(failures),
                                                     (": %s" % failures) if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
