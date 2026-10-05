// Driver for FPDF_SaveIncrementalObjects (fork patch incremental_objects.patch).
//
// usage: incr_driver IN OUT PAGE l,b,r,t [--mode M] [--repeat N]
//                    [--list n,n,...] [--password PW] [--avail] [--render]
// modes:
//   edit   : add one Square (inline dict, own /AP via FPDFAnnot_SetAP) on PAGE;
//            listed = /Annots array if indirect, else the page dict.
//   touch  : list the page dict unmodified (serialisation fidelity).
//   noop   : empty list.
//   list   : no edit, list exactly --list (refusal tests).
//   newdoc : FPDF_CreateNewDocument + one page, then save (must refuse).
//   removed: create a Square with /AP, remove it again, then do "edit";
//            the removed annotation's /AP stream must not be written.
//   stdfont: FPDFText_LoadStandardFont (unused), then "edit"; the font must
//            not be written (in chromium/8076 it is a direct dict anyway).
//   apreplaced: "edit", then FPDFAnnot_SetAP again; the first /AP stream is
//            an unreferenced new object and must not be written.
//   --list n,..: with mode list, exactly these; with other modes, appended
//            to the objects the mode lists.
//   session: ONE open document, several saves (no reopen); needs >= 3 pages.
//            Writes OUT.s1.pdf (edit p0, L1), OUT.s2.pdf (edit p1 + /Rotate p2,
//            L1+L2+L3), tries L2+L3 only (expected refusal) and writes
//            OUT.s2_noL3.pdf (L1+L2: TRUE, but the p2 rotation is lost).
//   --repeat N: N saves, each one re-opening the previous output.
//   --avail   : load through FPDFAvail (all data available) instead of
//               FPDF_LoadMemDocument64.
//   --render  : render PAGE with annotations before editing.
//   Every successful save is checked with FPDF_VerifyIncrementalSave on the
//   same open document ("verify", "mismatch", "verify_ms" in RESULT).
//   --corrupt nm|startxref: verify a corrupted copy instead (the update's
//               first "incr-real-" becomes "incr-REAL-", or the final
//               startxref value is incremented); OUT stays uncorrupted.
//   --fail-after N: the FPDF_FILEWRITE refuses any block that would take the
//               output past N bytes (write-failure tests; PDFium buffers
//               32 KiB, so small files only write in the final flush).
// Prints "RESULT {json}" for harness/run_baseline.py. Exit 0 on success,
// 2 on refusal (no OUT written), 1 on other errors.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "fpdf_annot.h"
#include "fpdf_dataavail.h"
#include "fpdf_edit.h"
#include "fpdf_save.h"
#include "fpdfview.h"

typedef struct {
  FPDF_FILEWRITE fw;
  unsigned char* data;
  size_t len, cap;
} MemWriter;

static size_t g_fail_after = (size_t)-1;

static int WriteBlockCb(FPDF_FILEWRITE* self, const void* d, unsigned long n) {
  MemWriter* w = (MemWriter*)self;
  if (w->len + n > g_fail_after) return 0;
  if (w->len + n > w->cap) {
    size_t nc = (w->cap ? w->cap * 2 : 1 << 16);
    while (nc < w->len + n) nc *= 2;
    unsigned char* p = realloc(w->data, nc);
    if (!p) return 0;
    w->data = p;
    w->cap = nc;
  }
  memcpy(w->data + w->len, d, n);
  w->len += n;
  return 1;
}

static unsigned char* ReadAll(const char* path, size_t* len) {
  FILE* f = fopen(path, "rb");
  if (!f) return NULL;
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  unsigned char* b = malloc(n ? n : 1);
  if (fread(b, 1, n, f) != (size_t)n) { fclose(f); free(b); return NULL; }
  fclose(f);
  *len = (size_t)n;
  return b;
}

static int WriteAll(const char* path, const unsigned char* d, size_t n) {
  FILE* f = fopen(path, "wb");
  if (!f) return 0;
  int ok = fwrite(d, 1, n, f) == n;
  return fclose(f) == 0 && ok;
}

static double NowMs(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static void ToWide(const char* s, FPDF_WCHAR* out, size_t cap) {
  size_t i = 0;
  for (; s[i] && i + 1 < cap; ++i) out[i] = (FPDF_WCHAR)(unsigned char)s[i];
  out[i] = 0;
}

// ---- FPDFAvail loading (everything available) ----
typedef struct { const unsigned char* d; size_t n; } Buf;
static FPDF_BOOL IsAvail(FX_FILEAVAIL* a, size_t off, size_t sz) { return 1; }
static void AddSeg(FX_DOWNLOADHINTS* h, size_t off, size_t sz) {}
static int GetBlock(void* p, unsigned long pos, unsigned char* out,
                    unsigned long sz) {
  Buf* b = (Buf*)p;
  if (pos + sz > b->n) return 0;
  memcpy(out, b->d + pos, sz);
  return 1;
}
static FPDF_AVAIL g_avail;
static Buf g_buf;
static FX_FILEAVAIL g_fa = {1, IsAvail};
static FPDF_FILEACCESS g_acc;
static FX_DOWNLOADHINTS g_hints = {1, AddSeg};

static FPDF_DOCUMENT Load(const unsigned char* in, size_t len, const char* pw,
                          int avail, int* linearized) {
  *linearized = -1;
  if (!avail) return FPDF_LoadMemDocument64(in, len, pw);
  g_buf.d = in;
  g_buf.n = len;
  g_acc.m_FileLen = (unsigned long)len;
  g_acc.m_GetBlock = GetBlock;
  g_acc.m_Param = &g_buf;
  g_avail = FPDFAvail_Create(&g_fa, &g_acc);
  while (FPDFAvail_IsDocAvail(g_avail, &g_hints) == PDF_DATA_NOTAVAIL) {}
  *linearized = FPDFAvail_IsLinearized(g_avail);
  FPDF_DOCUMENT doc = FPDFAvail_GetDocument(g_avail, pw);
  if (doc) {
    for (int i = 0; i < FPDF_GetPageCount(doc); ++i)
      while (FPDFAvail_IsPageAvail(g_avail, i, &g_hints) == PDF_DATA_NOTAVAIL) {}
  }
  return doc;
}
static void Close(FPDF_DOCUMENT doc) {
  FPDF_CloseDocument(doc);
  if (g_avail) { FPDFAvail_Destroy(g_avail); g_avail = NULL; }
}

// Adds a Square with its own /AP. Returns the object that holds /Annots
// (indirect /Annots array, else page dict), 0 on error.
static uint32_t AddSquare(FPDF_PAGE page, const float rect[4], const char* nm,
                          FPDF_ANNOTATION* keep) {
  uint32_t annots = FPDFPage_GetAnnotsObjectNumber(page);
  uint32_t holder = annots ? annots : FPDFPage_GetObjectNumber(page);
  FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
  FS_RECTF r = {rect[0], rect[3], rect[2], rect[1]};  // l, t, r, b
  int ok = a && FPDFAnnot_SetRect(a, &r) &&
           FPDFAnnot_SetColor(a, FPDFANNOT_COLORTYPE_Color, 255, 0, 0, 255) &&
           FPDFAnnot_SetBorder(a, 0, 0, 2) &&
           FPDFAnnot_SetFlags(a, FPDF_ANNOT_FLAG_PRINT);
  char ap[256];
  FPDF_WCHAR wbuf[256];
  ToWide(nm, wbuf, 256);
  ok = ok && FPDFAnnot_SetStringValue(a, "NM", wbuf);
  // Form BBox == /Rect (FPDFAnnot_SetAP), so draw in page coordinates.
  snprintf(ap, sizeof ap, "q 1 0 0 RG 2 w %.2f %.2f %.2f %.2f re S Q",
           rect[0] + 1, rect[1] + 1, rect[2] - rect[0] - 2,
           rect[3] - rect[1] - 2);
  ToWide(ap, wbuf, 256);
  ok = ok && FPDFAnnot_SetAP(a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, wbuf);
  if (keep) *keep = a;
  else if (a) FPDFPage_CloseAnnot(a);
  return ok ? holder : 0;
}

static int Save(FPDF_DOCUMENT doc, const uint32_t* l, size_t n, MemWriter* w,
                double* ms) {
  memset(w, 0, sizeof *w);
  w->fw.version = 1;
  w->fw.WriteBlock = WriteBlockCb;
  double t0 = NowMs();
  int ok = FPDF_SaveIncrementalObjects(doc, &w->fw, n ? l : NULL, n);
  *ms = NowMs() - t0;
  return ok;
}

static const char* g_corrupt = NULL;

// FPDF_VerifyIncrementalSave on a copy of `data` (optionally corrupted).
static int Verify(FPDF_DOCUMENT doc, const unsigned char* data, size_t len,
                  size_t orig_len, uint32_t* mismatch, double* ms) {
  unsigned char* copy = malloc(len ? len : 1);
  memcpy(copy, data, len);
  if (g_corrupt && !strcmp(g_corrupt, "nm")) {
    int done = 0;
    for (size_t i = orig_len; i + 10 <= len && !done; ++i) {
      if (!memcmp(copy + i, "incr-real-", 10)) {
        memcpy(copy + i, "incr-REAL-", 10);
        done = 1;
      }
    }
    // Encrypted file: the /NM string is ciphertext. Flip one byte inside
    // it (past the AES IV), avoiding string delimiters and escapes.
    for (size_t i = orig_len; i + 4 <= len && !done; ++i) {
      if (memcmp(copy + i, "/NM(", 4)) continue;
      for (size_t p = i + 4 + 20; p < len; ++p) {
        unsigned char c = copy[p], f = c ^ 1;
        if (strchr("()\\", c) || strchr("()\\", f) || copy[p - 1] == '\\' ||
            c == 0 || f == 0)
          continue;
        copy[p] = f;
        done = 1;
        break;
      }
    }
  } else if (g_corrupt && !strcmp(g_corrupt, "startxref")) {
    for (size_t i = len; i-- > 9;) {
      if (!memcmp(copy + i - 9, "startxref", 9)) {
        size_t j = i;
        while (j < len && (copy[j] < '0' || copy[j] > '9')) ++j;
        size_t k = j;
        while (k < len && copy[k] >= '0' && copy[k] <= '9') ++k;
        if (k > j) copy[k - 1] = copy[k - 1] == '9' ? '0' : copy[k - 1] + 1;
        break;
      }
    }
  }
  Buf b = {copy, len};
  FPDF_FILEACCESS acc = {(unsigned long)len, GetBlock, &b};
  double t0 = NowMs();
  int ok = FPDF_VerifyIncrementalSave(doc, &acc, mismatch);
  *ms = NowMs() - t0;
  free(copy);
  return ok;
}

// session mode (see header). Prints its own RESULT.
static int Session(const unsigned char* in, size_t len, const char* out,
                   const float rect[4], const char* pw, int avail) {
  int lin;
  FPDF_DOCUMENT doc = Load(in, len, pw, avail, &lin);
  if (!doc || FPDF_GetPageCount(doc) < 3) return 1;
  char path[4096];
  MemWriter w;
  double ms;
  uint32_t L[8];
  FPDF_PAGE p0 = FPDF_LoadPage(doc, 0);
  L[0] = AddSquare(p0, rect, "session-p0", NULL);
  FPDF_ClosePage(p0);
  uint32_t mm1 = 0, mm2 = 0, mm3 = 0;
  double vms;
  int s1 = Save(doc, L, 1, &w, &ms);
  int v1 = s1 && Verify(doc, w.data, w.len, len, &mm1, &vms);
  snprintf(path, sizeof path, "%s.s1.pdf", out);
  if (s1) WriteAll(path, w.data, w.len);
  free(w.data);
  FPDF_PAGE p1 = FPDF_LoadPage(doc, 1);
  L[1] = AddSquare(p1, rect, "session-p1", NULL);
  FPDF_ClosePage(p1);
  FPDF_PAGE p2 = FPDF_LoadPage(doc, 2);
  FPDFPage_SetRotation(p2, 1);  // changes only the existing page dict
  L[2] = FPDFPage_GetObjectNumber(p2);
  FPDF_ClosePage(p2);
  int s2 = Save(doc, L, 3, &w, &ms);  // cumulative L1+L2+L3
  int v2 = s2 && Verify(doc, w.data, w.len, len, &mm2, &vms);
  snprintf(path, sizeof path, "%s.s2.pdf", out);
  if (s2) WriteAll(path, w.data, w.len);
  free(w.data);
  int s2_only = Save(doc, L + 1, 2, &w, &ms);  // L2+L3 only
  size_t only_len = w.len;
  free(w.data);
  int s2_nol3 = Save(doc, L, 2, &w, &ms);  // L1+L2, rotation not listed
  int v3 = s2_nol3 && Verify(doc, w.data, w.len, len, &mm3, &vms);
  snprintf(path, sizeof path, "%s.s2_noL3.pdf", out);
  if (s2_nol3) WriteAll(path, w.data, w.len);
  free(w.data);
  Close(doc);
  printf("RESULT {\"mode\":\"session\",\"L\":[%u,%u,%u],\"s1\":%d,"
         "\"s2_cumulative\":%d,\"s2_only_L2L3\":%d,\"s2_only_bytes\":%zu,"
         "\"s2_noL3\":%d,\"verify_s1\":%d,\"mismatch_s1\":%u,"
         "\"verify_s2\":%d,\"mismatch_s2\":%u,\"verify_s2_noL3\":%d,"
         "\"mismatch_s2_noL3\":%u,\"status\":\"ok\"}\n",
         L[0], L[1], L[2], s1, s2, s2_only, only_len, s2_nol3, v1, mm1, v2,
         mm2, v3, mm3);
  return 0;
}

// One save. Returns 1 ok, 0 refused, -1 error. Fills *out/*out_len.
static int OneSave(const unsigned char* in, size_t in_len, int page_no,
                   const float rect[4], const char* mode, const uint32_t* list,
                   size_t list_n, const char* pw, int k, int avail, int render,
                   unsigned char** out, size_t* out_len, char* info,
                   size_t info_cap) {
  int lin = -1;
  FPDF_DOCUMENT doc;
  if (!strcmp(mode, "newdoc")) {
    doc = FPDF_CreateNewDocument();
    FPDF_PAGE p = doc ? FPDFPage_New(doc, 0, 612, 792) : NULL;
    if (p) FPDF_ClosePage(p);
  } else {
    doc = Load(in, in_len, pw, avail, &lin);
  }
  if (!doc) {
    snprintf(info, info_cap, "\"load_error\":%lu", FPDF_GetLastError());
    if (g_avail) { FPDFAvail_Destroy(g_avail); g_avail = NULL; }
    return -1;
  }
  uint32_t objnums[64];
  size_t n = 0;
  uint32_t page_num = 0, annots_num = 0;
  int edit = !strcmp(mode, "edit") || !strcmp(mode, "removed") ||
             !strcmp(mode, "stdfont") || !strcmp(mode, "apreplaced");
  if (edit || !strcmp(mode, "touch") || render) {
    FPDF_PAGE page = FPDF_LoadPage(doc, page_no);
    if (!page) { Close(doc); return -1; }
    if (render) {
      int bw = 200, bh = 200;
      FPDF_BITMAP bmp = FPDFBitmap_Create(bw, bh, 0);
      FPDFBitmap_FillRect(bmp, 0, 0, bw, bh, 0xFFFFFFFF);
      FPDF_RenderPageBitmap(bmp, page, 0, 0, bw, bh, 0, FPDF_ANNOT);
      FPDFBitmap_Destroy(bmp);
    }
    page_num = FPDFPage_GetObjectNumber(page);
    annots_num = FPDFPage_GetAnnotsObjectNumber(page);
    if (!strcmp(mode, "touch")) {
      if (page_num) objnums[n++] = page_num;
    } else if (edit) {
      if (!strcmp(mode, "removed")) {
        FPDF_ANNOTATION tmp = NULL;
        AddSquare(page, rect, "removed", &tmp);
        if (tmp) FPDFPage_CloseAnnot(tmp);
        FPDFPage_RemoveAnnot(page, FPDFPage_GetAnnotCount(page) - 1);
      } else if (!strcmp(mode, "stdfont")) {
        FPDF_FONT f = FPDFText_LoadStandardFont(doc, "Helvetica");
        if (f) FPDFFont_Close(f);
      }
      char nm[32];
      snprintf(nm, sizeof nm, "incr-real-%d", k);
      FPDF_ANNOTATION a = NULL;
      uint32_t holder = AddSquare(page, rect, nm, &a);
      if (a && !strcmp(mode, "apreplaced")) {
        FPDF_WCHAR wbuf[64];
        ToWide("q 0 0 1 RG 1 w 0 0 m 1 1 l S Q", wbuf, 64);
        if (!FPDFAnnot_SetAP(a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, wbuf))
          holder = 0;
      }
      if (a) FPDFPage_CloseAnnot(a);
      if (!holder) { FPDF_ClosePage(page); Close(doc); return -1; }
      objnums[n++] = holder;
    }
    FPDF_ClosePage(page);
  }
  for (size_t i = 0; i < list_n && n < 64; ++i) objnums[n++] = list[i];
  MemWriter w;
  double ms;
  FPDF_BOOL ok = Save(doc, objnums, n, &w, &ms);
  uint32_t mismatch = 0;
  double vms = 0;
  int verified = ok && Verify(doc, w.data, w.len, in_len, &mismatch, &vms);
  Close(doc);
  int pos = snprintf(info, info_cap,
                     "\"ok\":%s,\"save_ms\":%.2f,\"page_objnum\":%u,"
                     "\"annots_objnum\":%u,\"avail_linearized\":%d,"
                     "\"bytes_written\":%zu,\"verify\":%s,\"mismatch\":%u,"
                     "\"verify_ms\":%.2f,\"objnums\":[",
                     ok ? "true" : "false", ms, page_num, annots_num, lin, w.len,
                     verified ? "true" : "false", mismatch, vms);
  for (size_t i = 0; i < n && pos < (int)info_cap; ++i)
    pos += snprintf(info + pos, info_cap - pos, "%s%u", i ? "," : "", objnums[i]);
  if (pos < (int)info_cap) snprintf(info + pos, info_cap - pos, "]");
  if (!ok) { free(w.data); return 0; }
  *out = w.data;
  *out_len = w.len;
  return 1;
}

int main(int argc, char** argv) {
  if (argc < 5) {
    fprintf(stderr, "usage: %s IN OUT PAGE l,b,r,t [--mode M] [--repeat N] "
                    "[--list n,..] [--password PW] [--avail] [--render]\n",
            argv[0]);
    return 1;
  }
  const char *in_path = argv[1], *out_path = argv[2], *mode = "edit", *pw = "";
  int page_no = atoi(argv[3]), repeat = 1, avail = 0, render = 0;
  float rect[4];
  if (sscanf(argv[4], "%f,%f,%f,%f", &rect[0], &rect[1], &rect[2], &rect[3]) != 4)
    return 1;
  uint32_t list[64];
  size_t list_n = 0;
  for (int i = 5; i < argc; ++i) {
    if (!strcmp(argv[i], "--avail")) { avail = 1; continue; }
    if (!strcmp(argv[i], "--render")) { render = 1; continue; }
    if (i + 1 >= argc) break;
    if (!strcmp(argv[i], "--mode")) mode = argv[++i];
    else if (!strcmp(argv[i], "--repeat")) repeat = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--password")) pw = argv[++i];
    else if (!strcmp(argv[i], "--corrupt")) g_corrupt = argv[++i];
    else if (!strcmp(argv[i], "--fail-after"))
      g_fail_after = (size_t)strtoull(argv[++i], NULL, 10);
    else if (!strcmp(argv[i], "--list")) {
      char* s = argv[++i];
      while (*s && list_n < 64) {
        list[list_n++] = (uint32_t)strtoul(s, &s, 10);
        if (*s == ',') ++s;
      }
    }
  }
  FPDF_LIBRARY_CONFIG cfg = {2, NULL, NULL, 0};
  FPDF_InitLibraryWithConfig(&cfg);
  size_t len = 0;
  unsigned char* data = ReadAll(in_path, &len);
  if (!data) return 1;
  if (!strcmp(mode, "session")) {
    int r = Session(data, len, out_path, rect, pw, avail);
    free(data);
    FPDF_DestroyLibrary();
    return r;
  }
  double total = 0;
  printf("RESULT {\"mode\":\"%s\",\"repeat\":%d,\"saves\":[", mode, repeat);
  int rc = 0;
  for (int k = 0; k < repeat; ++k) {
    unsigned char* out = NULL;
    size_t out_len = 0;
    char info[2048];
    double t0 = NowMs();
    int r = OneSave(data, len, page_no, rect, mode, list, list_n, pw, k, avail,
                    render, &out, &out_len, info, sizeof info);
    total += NowMs() - t0;
    printf("%s{%s}", k ? "," : "", info);
    if (r != 1) { rc = r == 0 ? 2 : 1; break; }
    free(data);
    data = out;
    len = out_len;
  }
  printf("],\"timing\":{\"save_ms\":%.1f},\"status\":\"%s\"}\n", total,
         rc == 0 ? "ok" : rc == 2 ? "refused" : "error");
  if (rc == 0 && !WriteAll(out_path, data, len)) rc = 1;
  free(data);
  FPDF_DestroyLibrary();
  return rc;
}
