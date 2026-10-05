#!/usr/bin/env python3
"""Fixtures for the FPDF_SaveIncrementalObjects scripted test.

usage: make_fixtures.py OUTDIR --qpdf PATH
Requires pikepdf (encrypted fixtures) and qpdf (linearized fixture); fails
if either is missing. data/hybrid_xrefstm.pdf is a synthetic hybrid-reference
file (classic table + /XRefStm) from the Rapida save-spike corpus.
"""
import os, re, shutil, subprocess, sys, zlib

try:
    import pikepdf
except ImportError:
    sys.exit("make_fixtures.py: pikepdf is required (pip install pikepdf)")
OUT = sys.argv[1]
QPDF = sys.argv[sys.argv.index("--qpdf") + 1] if "--qpdf" in sys.argv else shutil.which("qpdf")
if not QPDF or not os.path.exists(QPDF):
    sys.exit("make_fixtures.py: qpdf is required (--qpdf PATH)")
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)
ID = b"/ID [<00112233445566778899aabbccddeeff><00112233445566778899aabbccddeeff>]"


def classic(objs, root=1, gens=None, tail=b"\n", size=None, prefix=b""):
    gens = gens or {}
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = {}
    for n in sorted(objs):
        offs[n] = len(out)
        out += b"%d %d obj\n" % (n, gens.get(n, 0)) + objs[n] + b"\nendobj\n"
    nent = max(objs) + 1
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f\r\n" % nent
    for n in range(1, nent):
        out += (b"%010d %05d n\r\n" % (offs[n], gens.get(n, 0))) if n in offs else b"0000000000 00000 f\r\n"
    out += b"trailer\n<< /Size %d /Root %d 0 R %s >>\n" % (size or nent, root, ID)
    out += b"startxref\n%d\n%%%%EOF" % x + tail
    return prefix + bytes(out)


def xref_stream(objs, root=1, objstm_members=()):
    """xref stream + one ObjStm holding objstm_members (non-stream objects)."""
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offs, comp = {}, {}
    plain = [n for n in sorted(objs) if n not in objstm_members]
    for n in plain:
        offs[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    stm = max(objs) + 1
    hdr, body = b"", b""
    for i, n in enumerate(objstm_members):
        hdr += b"%d %d " % (n, len(body))
        body += objs[n] + b"\n"
        comp[n] = (stm, i)
    data = zlib.compress(hdr + body)
    offs[stm] = len(out)
    out += (b"%d 0 obj\n<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n"
            % (stm, len(objstm_members), len(hdr), len(data)) + data + b"\nendstream\nendobj\n")
    xn = stm + 1
    offs[xn] = len(out)
    rows = b"\x00\x00\x00\x00\xff\xff"
    for n in range(1, xn + 1):
        if n in comp:
            rows += b"\x02" + comp[n][0].to_bytes(4, "big") + comp[n][1].to_bytes(1, "big")
        else:
            rows += b"\x01" + offs[n].to_bytes(4, "big") + b"\x00"
    out += (b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 1] /Root %d 0 R %s /Length %d >>\nstream\n"
            % (xn, xn + 1, root, ID, len(rows)) + rows + b"\nendstream\nendobj\n")
    out += b"startxref\n%d\n%%%%EOF\n" % offs[xn]
    return bytes(out)


CONTENT = b"0 0 1 rg 100 100 200 200 re f\n"
STREAM = b"<< /Length %d >>\nstream\n" % len(CONTENT) + CONTENT + b"endstream"


def pages3():
    return {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << >> >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << >> >>",
            5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << >> >>",
            6: STREAM}


def page1(page_gen=0, contents_gen=0, extra=False):
    o = {1: b"<< /Type /Catalog /Pages 2 0 R%s >>" % (b" /Foo 5 1 R" if extra else b""),
         2: b"<< /Type /Pages /Kids [3 %d R] /Count 1 >>" % page_gen,
         3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 %d R /Resources << >> >>" % contents_gen,
         4: STREAM}
    if extra:
        o[5] = b"<< /Unrelated true >>"
    return o


def w(name, data):
    open(os.path.join(OUT, name), "wb").write(data)


w("basic_classic.pdf", classic(pages3()))
w("basic_xrefstm.pdf", xref_stream(pages3(), objstm_members=(1, 2, 3, 4, 5)))
w("gen1_page.pdf", classic(page1(page_gen=1), gens={3: 1}))
w("gen1_ref.pdf", classic(page1(contents_gen=1), gens={4: 1}))
w("gen1_unrelated.pdf", classic(page1(extra=True), gens={5: 1}))
w("no_final_eol.pdf", classic(page1(), tail=b""))
w("junk_prefix.pdf", classic(page1(), prefix=b"JUNK-BEFORE-HEADER\n" * 3))
o = page1(); o[6] = b"<< /Other true >>"
w("free_entry.pdf", classic(o))  # object 5 is free
w("huge_size.pdf", classic(page1(), size=99999999))
w("direct_kid.pdf", classic({
    1: b"<< /Type /Catalog /Pages 2 0 R >>",
    2: b"<< /Type /Pages /Kids [<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << >> >>] /Count 1 >>",
    3: STREAM}))
for name in ("basic_classic.pdf", "basic_xrefstm.pdf"):
    d = open(os.path.join(OUT, name), "rb").read()
    i = d.rfind(b"startxref")
    sx = int(re.match(rb"startxref\s+(\d+)", d[i:]).group(1))
    w("damaged_" + name[6:], d[:i] + b"startxref\n%d\n%%%%EOF\n" % (sx + 13))

pdf = pikepdf.open(os.path.join(OUT, "basic_classic.pdf"))
with pdf.open_metadata() as meta:
    meta["dc:title"] = "incr fixture"
pdf.save(os.path.join(OUT, "enc_aes128_metadata.pdf"),
         encryption=pikepdf.Encryption(user="", owner="owner", R=4, aes=True, metadata=True))
p = pikepdf.open(os.path.join(OUT, "enc_aes128_metadata.pdf"))
open(os.path.join(OUT, "enc_aes128_metadata.nums"), "w").write(
    "%d %d\n" % (p.Root.Metadata.objgen[0], p.trailer.Encrypt.objgen[0]))
pikepdf.open(os.path.join(OUT, "basic_classic.pdf")).save(
    os.path.join(OUT, "enc_rc4_r3.pdf"),
    encryption=pikepdf.Encryption(user="", owner="owner", R=3, aes=False, metadata=False))
pikepdf.open(os.path.join(OUT, "basic_classic.pdf")).save(
    os.path.join(OUT, "enc_aes256_r6.pdf"),
    encryption=pikepdf.Encryption(user="", owner="owner", R=6))
subprocess.run([QPDF, "--linearize", os.path.join(OUT, "basic_classic.pdf"),
                os.path.join(OUT, "linearized.pdf")], check=True)
shutil.copy(os.path.join(HERE, "data", "hybrid_xrefstm.pdf"), OUT)

# Indirect /Length with generation 1, unfiltered and filtered stream.
def length_gen1(filtered):
    data = zlib.compress(CONTENT) if filtered else CONTENT
    o = page1()
    o[4] = (b"<< /Length 5 1 R%s >>\nstream\n" % (b" /Filter /FlateDecode" if filtered else b"")
            + data + b"\nendstream")
    o[5] = b"%d" % len(data)
    return classic(o, gens={5: 1})
w("gen1_length_unfiltered.pdf", length_gen1(False))
w("gen1_length_filtered.pdf", length_gen1(True))

# > 32 KiB so that the save flushes several times (write-failure cases).
import random
random.seed(1)
big = b"".join(b"%% pad %08x\n" % random.getrandbits(32) for _ in range(9000))
o = pages3(); o[6] = b"<< /Length %d >>\nstream\n" % (len(CONTENT) + len(big)) + CONTENT + big + b"endstream"
w("big_classic.pdf", classic(o))
