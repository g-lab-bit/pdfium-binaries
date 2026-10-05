#!/usr/bin/env python3
"""Independent structural check of the update appended by FPDF_SaveIncrementalObjects.

usage: check_update.py ORIG SAVED [--chain]
Parses the bytes after the original (no PDFium involved) and reports:
  objs        object numbers defined in the appended part (per update section)
  xref_kind   table | stream for each appended section; orig_kind = original last section
  prev_ok     /Prev of the first appended section == original startxref (or no /Prev if damaged)
  entries_ok  every in-use xref entry of the appended sections points at "N G obj" with N matching
  self_entry  xref stream lists its own object number at its own offset
  w           /W of xref streams; id0_same: /ID[0] unchanged; encrypt_same: same /Encrypt ref
"""
import json, re, sys

OBJ = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")


def last_startxref(d):
    i = d.rfind(b"startxref")
    m = re.match(rb"startxref\s+(\d+)", d[i:])
    return int(m.group(1)) if m else None


def kind_at(d, off):
    t = d[off:off + 32].lstrip()
    if t.startswith(b"xref"):
        return "table"
    if OBJ.match(t) and re.search(rb"/Type\s*/XRef", d[off:off + 4096]):
        return "stream"
    return "invalid"


def trailer_of(d, off, kind):
    if kind == "table":
        t = d.index(b"trailer", off)
        return d[t:d.index(b"startxref", t)]
    return d[off:d.index(b"stream", off)]


def get(tr, key, pat=rb"[^/>]*"):
    m = re.search(rb"/" + key + rb"(?![A-Za-z])\s*(" + pat + rb")", tr)
    return m.group(1).strip() if m else None


def table_entries(d, off):
    p = d.index(b"xref", off) + 4
    t = d.index(b"trailer", p)
    tok = d[p:t].split()
    i, ents = 0, {}
    while i < len(tok):
        s, c = int(tok[i]), int(tok[i + 1]); i += 2
        for k in range(c):
            o, g, ty = int(tok[i]), int(tok[i + 1]), tok[i + 2]; i += 3
            ents[s + k] = (ty.decode(), o, g)
    return ents


def stream_entries(d, off):
    tr = trailer_of(d, off, "stream")
    w = [int(x) for x in get(tr, b"W", rb"\[[^\]]*\]").strip(b"[]").split()]
    idx = [int(x) for x in get(tr, b"Index", rb"\[[^\]]*\]").strip(b"[]").split()]
    ln = int(get(tr, b"Length", rb"\d+"))
    s = d.index(b"stream", off) + 6
    s += 2 if d[s:s + 2] == b"\r\n" else 1
    data = d[s:s + ln]
    if b"/Filter" in tr:
        import zlib
        data = zlib.decompress(data)
    ents, pos, rs = {}, 0, sum(w)
    for a, c in zip(idx[0::2], idx[1::2]):
        for k in range(c):
            row = data[pos:pos + rs]; pos += rs
            vals, q = [], 0
            for wi in w:
                vals.append(int.from_bytes(row[q:q + wi], "big") if wi else (1 if not vals else 0)); q += wi
            ents[a + k] = ({0: "f", 1: "n", 2: "c"}[vals[0]], vals[1], vals[2])
    return ents, w, pos == len(data)


def pdfstr(tok):
    """Decode a PDF hex or literal string token to bytes."""
    if tok.startswith(b"<"):
        h = re.sub(rb"\s", b"", tok[1:-1])
        return bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
    out, i, body = bytearray(), 0, tok[1:-1]
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(body):
        c = body[i]
        if c == 0x5C:
            i += 1; c = body[i]
            if c in esc: out.append(esc[c])
            elif 0x30 <= c <= 0x37:
                j = i
                while j < len(body) and j < i + 3 and 0x30 <= body[j] <= 0x37: j += 1
                out.append(int(body[i:j], 8) & 0xFF); i = j; continue
            elif c in (10, 13): pass
            else: out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out)


def main():
    orig, saved = open(sys.argv[1], "rb").read(), open(sys.argv[2], "rb").read()
    r = {"prefix": saved.startswith(orig), "added": len(saved) - len(orig)}
    if not r["prefix"] or len(saved) == len(orig):
        r["identical"] = saved == orig
        print(json.dumps(r)); return
    # Bytes before "%PDF-": positions are relative to the header (PDFium's
    # convention), so parse both files from the header on.
    h = orig.find(b"%PDF-")
    r["header_offset"] = h
    orig, saved = orig[h:], saved[h:]
    osx = last_startxref(orig)
    r["orig_kind"] = kind_at(orig, osx) if osx else "none"
    otr = trailer_of(orig, osx, r["orig_kind"]) if r["orig_kind"] != "invalid" and osx else b""
    # walk the appended sections backwards from the final startxref
    secs, off = [], last_startxref(saved)
    while off is not None and off >= len(orig):
        k = kind_at(saved, off)
        tr = trailer_of(saved, off, k)
        prev = get(tr, b"Prev", rb"\d+")
        secs.append((off, k, tr))
        off = int(prev) if prev else None
    secs.reverse()
    r["sections"] = []
    entries_ok, bounds = True, [len(orig)] + [s[0] for s in secs]
    for n, (off, k, tr) in enumerate(secs):
        body = saved[bounds[n]:off]
        objs = sorted(int(m.group(1)) for m in OBJ.finditer(body))
        gens = sorted({int(m.group(2)) for m in OBJ.finditer(body)})
        if k == "table":
            ents, w, exact = table_entries(saved, off), None, True
        else:
            ents, w, exact = stream_entries(saved, off)
        for num, (ty, o, g) in ents.items():
            if ty == "n":
                m = OBJ.match(saved[o:o + 32])
                if not m or int(m.group(1)) != num:
                    entries_ok = False
        sec = {"kind": k, "objs": objs, "gens": gens, "entries": len(ents),
               "entries_cover_objs": set(objs) <= {x for x, e in ents.items() if e[0] == "n"},
               "prev": get(tr, b"Prev", rb"\d+").decode() if get(tr, b"Prev", rb"\d+") else None,
               "size": int(get(tr, b"Size", rb"\d+")), "has_type_xref": bool(re.search(rb"/Type\s*/XRef", tr))}
        if k == "stream":
            selfnum = int(OBJ.match(saved[off:off + 32]).group(1))
            sec.update(w=w, data_len_exact=exact,
                       self_entry=ents.get(selfnum, ("?", -1, 0))[1] == off,
                       self_num=selfnum)
        r["sections"].append(sec)
    r["entries_ok"] = entries_ok
    first = secs[0] if secs else None
    if first:
        ftr = first[2]
        r["prev_eq_orig_startxref"] = get(ftr, b"Prev", rb"\d+") == (str(osx).encode() if osx else None)
        oid = re.search(rb"/ID\s*\[\s*(<[^>]*>|\((?:\\.|[^\\)])*\))", otr)
        nid = re.search(rb"/ID\s*\[\s*(<[^>]*>|\((?:\\.|[^\\)])*\))", ftr)
        r["id0_same"] = (pdfstr(oid.group(1)) == pdfstr(nid.group(1))) if oid and nid else (None if not oid else False)
        oe, ne = get(otr, b"Encrypt", rb"\d+\s+\d+\s+R"), get(ftr, b"Encrypt", rb"\d+\s+\d+\s+R")
        r["encrypt_same"] = oe == ne
        osz = get(otr, b"Size", rb"\d+")
        r["size_ge_orig"] = osz is None or int(get(ftr, b"Size", rb"\d+")) >= int(osz)
    print(json.dumps(r))


if __name__ == "__main__":
    main()
