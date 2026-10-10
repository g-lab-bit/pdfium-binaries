// Driver for object_syntax_api.patch and annot_tracker_scope.patch (fork-p16).
//
//   driver syntax IN OUT
//       Adds a Square annotation on page 0, writes /CL, /IT and /Measure with
//       FPDFAnnot_SetObjectSyntax and a page /VP with FPDFPage_SetObjectSyntax,
//       reads them back, tries the refusals, removes /IT; full save to OUT.
//       Prints JSON.
//   driver tracker IN N
//       Adds N Square annotations on page 0 (direct dictionaries in /Annots),
//       then FPDFAnnot_SetOCMembership(OCG 0) on each; prints the total time
//       in ms and what the last call reported (FPDFDoc_GetLastModifiedObjects)
//       next to the page's object number.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "fpdf_annot.h"
#include "fpdf_doc.h"
#include "fpdf_edit.h"
#include "fpdf_save.h"
#include "fpdfview.h"

typedef struct {
  FPDF_FILEWRITE fw;
  unsigned char* data;
  size_t len, cap;
} MemWriter;

static int WriteBlockCb(FPDF_FILEWRITE* self, const void* d, unsigned long n) {
  MemWriter* w = (MemWriter*)self;
  if (w->len + n > w->cap) {
    size_t nc = w->cap ? w->cap * 2 : 1 << 16;
    while (nc < w->len + n) nc *= 2;
    w->data = realloc(w->data, nc);
    w->cap = nc;
  }
  memcpy(w->data + w->len, d, n);
  w->len += n;
  return 1;
}

static int SaveTo(FPDF_DOCUMENT doc, const char* path) {
  MemWriter w;
  memset(&w, 0, sizeof w);
  w.fw.version = 1;
  w.fw.WriteBlock = WriteBlockCb;
  int ok = FPDF_SaveAsCopy(doc, &w.fw, 0);
  FILE* f = ok ? fopen(path, "wb") : NULL;
  if (f) { fwrite(w.data, 1, w.len, f); ok = fclose(f) == 0; }
  free(w.data);
  return ok && f;
}

// Prints "key":"<value syntax>" (JSON-escaped quotes / backslashes).
static void PrintGot(const char* name, unsigned long n, const char* buf) {
  printf(",\"%s\":\"", name);
  for (unsigned long i = 0; n && i + 1 < n; ++i) {
    if (buf[i] == '"' || buf[i] == '\\') putchar('\\');
    putchar(buf[i] == '\n' || buf[i] == '\r' ? ' ' : buf[i]);
  }
  putchar('"');
}

static int Syntax(const char* in, const char* out) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
  FS_RECTF r = {100, 300, 200, 200};
  FPDFAnnot_SetRect(a, &r);
  const char* measure =
      "<< /Type /Measure /Subtype /RL /R (1 in = 10 ft) "
      "/X [<< /U (ft) /C 0.8333 /D 100 >>] /D [<< /U (ft) /C 1 /D 16 /F /F >>] >>";
  int cl = FPDFAnnot_SetObjectSyntax(doc, a, "CL", "[100 200 150 250]");
  int it = FPDFAnnot_SetObjectSyntax(doc, a, "IT", "/LineDimension");
  int me = FPDFAnnot_SetObjectSyntax(doc, a, "Measure", measure);
  int vp = FPDFPage_SetObjectSyntax(
      doc, page, "VP",
      "[<< /Type /Viewport /BBox [0 0 612 792] /Name (Plan) /Measure "
      "<< /Type /Measure /Subtype /RL /R (1:100) >> >>]");
  // Refusals: each must return false and change nothing.
  int ref1 = FPDFAnnot_SetObjectSyntax(doc, a, "X1", "1 0 R");
  int ref2 = FPDFAnnot_SetObjectSyntax(doc, a, "X2", "<< /A 1 0 R >>");
  int junk = FPDFAnnot_SetObjectSyntax(doc, a, "X3", "[1 2] trailing");
  int resv = FPDFAnnot_SetObjectSyntax(doc, a, "Subtype", "/Circle");
  int open_str = FPDFAnnot_SetObjectSyntax(doc, a, "X4", "(unterminated");
  int null_v = FPDFAnnot_SetObjectSyntax(doc, a, "X5", "null");
  int page_resv = FPDFPage_SetObjectSyntax(doc, page, "Annots", "[]");
  int stream_v = FPDFAnnot_SetObjectSyntax(doc, a, "X6", "<< /Length 0 >> stream\nendstream");
  char big_buf[256];
  unsigned long gcl = FPDFAnnot_GetObjectSyntax(a, "CL", big_buf, sizeof big_buf);
  char cl_text[256]; memcpy(cl_text, big_buf, sizeof cl_text);
  unsigned long need = FPDFAnnot_GetObjectSyntax(a, "Measure", NULL, 0);
  char* mbuf = malloc(need ? need : 1);
  unsigned long gme = FPDFAnnot_GetObjectSyntax(a, "Measure", mbuf, need);
  unsigned long gvp_need = FPDFPage_GetObjectSyntax(page, "VP", NULL, 0);
  unsigned long gx1 = FPDFAnnot_GetObjectSyntax(a, "X1", NULL, 0);
  int rm = FPDFAnnot_SetObjectSyntax(doc, a, "IT", "");
  unsigned long git = FPDFAnnot_GetObjectSyntax(a, "IT", NULL, 0);
  FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  int saved = SaveTo(doc, out);
  printf("{\"cl\":%d,\"it\":%d,\"measure\":%d,\"vp\":%d,\"ref1\":%d,\"ref2\":%d,"
         "\"junk\":%d,\"reserved\":%d,\"open_str\":%d,\"null\":%d,\"page_reserved\":%d,"
         "\"stream\":%d,\"removed\":%d,\"it_after\":%lu,\"x1_after\":%lu,\"vp_len\":%lu,"
         "\"measure_len\":%lu,\"saved\":%d",
         cl, it, me, vp, ref1, ref2, junk, resv, open_str, null_v, page_resv,
         stream_v, rm, git, gx1, gvp_need, gme, saved);
  PrintGot("cl_text", gcl, cl_text);
  PrintGot("measure_text", gme, mbuf);
  printf("}\n");
  free(mbuf);
  FPDF_CloseDocument(doc);
  return 0;
}

static double NowMs(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000.0 + t.tv_nsec / 1e6;
}

static int Tracker(const char* in, int n) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  for (int k = 0; k < n; ++k) {
    FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
    FS_RECTF r = {(float)(10 + (k % 50) * 10), (float)(20 + (k / 50) * 10),
                  (float)(16 + (k % 50) * 10), (float)(14 + (k / 50) * 10)};
    FPDFAnnot_SetRect(a, &r);
    FPDFPage_CloseAnnot(a);
  }
  int chain[1] = {0};
  int ok = 1;
  double t0 = NowMs();
  for (int k = 0; k < n; ++k) {
    FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, k);
    ok &= a && FPDFAnnot_SetOCMembership(doc, a, chain, 1);
    if (a) FPDFPage_CloseAnnot(a);
  }
  double ms = NowMs() - t0;
  uint32_t mod[8] = {0};
  int nmod = FPDFDoc_GetLastModifiedObjects(doc, mod, 8);
  printf("{\"ok\":%d,\"n\":%d,\"ms\":%.1f,\"nmod\":%d,\"mod0\":%u,\"page\":%u,\"annots\":%u}\n",
         ok, n, ms, nmod, nmod > 0 ? mod[0] : 0, FPDFPage_GetObjectNumber(page),
         FPDFPage_GetAnnotsObjectNumber(page));
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 3) return 1;
  FPDF_InitLibrary();
  int rc = 1;
  if (!strcmp(argv[1], "syntax") && argc >= 4) rc = Syntax(argv[2], argv[3]);
  else if (!strcmp(argv[1], "tracker") && argc >= 4) rc = Tracker(argv[2], atoi(argv[3]));
  FPDF_DestroyLibrary();
  return rc;
}
