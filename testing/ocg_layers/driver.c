// Driver for ocg_view_state.patch, annot_ocmd.patch and annot_ap_oc.patch.
//
//   driver make IN OUT
//       Adds a Square annotation (red filled rect appearance via
//       FPDFAnnot_AppendObject) at (300,300)-(400,400) on page 0, then
//       FPDFAnnot_SetOCMembership(WS, L) and FPDFAnnot_SetAPOptionalContent
//       (WS outermost, L inner); full save to OUT. OCG indices: 0 = WS, 1 = L.
//   driver render IN [i=s ...] [--print] [--clear-after]
//       Optional view states (FPDFDoc_SetOCGViewState), renders page 0 at 72
//       dpi with FPDF_ANNOT and prints the colour at the annotation centre and
//       at the page's own /OC L content (100,100) as JSON.
//   driver persist IN OUTPREFIX
//       Sets every view state to 0, then shows that nothing reaches the
//       file: full saves with and without the overrides are identical
//       (/ID masked), FPDF_SaveIncrementalObjects writes an exact copy and
//       FPDF_VerifyIncrementalSave is TRUE.
//   driver membership IN OUTPREFIX
//       FPDFAnnot_SetOCMembership cases on annotation 0, each saved to
//       OUTPREFIX.<case>.pdf for inspection; prints return values.
//   driver delete IN OUT INDEX...
//       FPDFDoc_DeleteOCG for each INDEX in turn (indices as at call time),
//       full save to OUT.
//   driver unmark IN OUT
//       FPDFAnnot_SetAPOptionalContent(count 0) on annotation 0.
//   driver incr IN OUT SCENARIO [indices...]
//       Edits, then FPDF_SaveIncrementalObjects (listing the /Annots holder)
//       and FPDF_VerifyIncrementalSave on the same open document:
//         new      - add a Square (AppendObject) on [indices]
//         rebuilt  - annotation 0 already in the file: SetAP(NULL) +
//                    AppendObject (green), then [indices]
//         reloaded - annotation 0 already in the file, re-marked as is
//         twice    - annotation 0 re-marked twice: [0,1], then [indices]
//   driver paths IN [i=s ...]
//       Renders page 0 through FPDF_RenderPageBitmap, the progressive
//       Start/Continue path, both with FPDF_RENDER_LOD_SKIP_SUBPIXEL, and
//       FPDF_FFLDraw (form environment); prints the annotation, page content
//       and widget (150,525) colours per path.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fpdf_annot.h"
#include "fpdf_doc.h"
#include "fpdf_edit.h"
#include "fpdf_formfill.h"
#include "fpdf_progressive.h"
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

static void Save(FPDF_DOCUMENT doc, FPDF_DWORD flags, MemWriter* w) {
  memset(w, 0, sizeof *w);
  w->fw.version = 1;
  w->fw.WriteBlock = WriteBlockCb;
  FPDF_SaveAsCopy(doc, &w->fw, flags);
}

static void MaskID(MemWriter* w) {
  for (size_t i = w->len; i-- > 3;) {
    if (!memcmp(w->data + i - 3, "/ID", 3)) {
      for (size_t j = i; j < w->len && w->data[j] != ']'; ++j) w->data[j] = '#';
      break;
    }
  }
}

static int WriteFile(const char* path, const MemWriter* w) {
  FILE* f = fopen(path, "wb");
  if (!f) return 0;
  fwrite(w->data, 1, w->len, f);
  return fclose(f) == 0;
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

typedef struct { const unsigned char* d; size_t n; } Buf;
static int GetBlock(void* p, unsigned long pos, unsigned char* out,
                    unsigned long sz) {
  Buf* b = (Buf*)p;
  if (pos + sz > b->n) return 0;
  memcpy(out, b->d + pos, sz);
  return 1;
}

static int Make(const char* in, const char* out) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
  FS_RECTF r = {300, 400, 400, 300};
  FPDF_PAGEOBJECT rect = FPDFPageObj_CreateNewRect(300, 300, 100, 100);
  int ok = a && FPDFAnnot_SetRect(a, &r) &&
           FPDFAnnot_SetFlags(a, FPDF_ANNOT_FLAG_PRINT) && rect &&
           FPDFPageObj_SetFillColor(rect, 255, 0, 0, 255) &&
           FPDFPath_SetDrawMode(rect, FPDF_FILLMODE_ALTERNATE, 0) &&
           FPDFAnnot_AppendObject(a, rect);
  int chain[2] = {0, 1};  // WS (ancestor, outermost), L
  int m = ok && FPDFAnnot_SetOCMembership(doc, a, chain, 2);
  int ap = ok && FPDFAnnot_SetAPOptionalContent(
                     doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, chain, 2);
  if (a) FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  MemWriter w;
  Save(doc, 0, &w);
  WriteFile(out, &w);
  free(w.data);
  FPDF_CloseDocument(doc);
  printf("{\"make\":%d,\"membership\":%d,\"ap_oc\":%d}\n", ok, m, ap);
  return ok && m && ap ? 0 : 1;
}

static void Pixel(FPDF_BITMAP bmp, int x, int y, char* out) {
  const unsigned char* p = (const unsigned char*)FPDFBitmap_GetBuffer(bmp) +
                           y * FPDFBitmap_GetStride(bmp) + x * 4;
  sprintf(out, "[%d,%d,%d]", p[2], p[1], p[0]);  // BGRA
}

static int Render(int argc, char** argv) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[2], "");
  if (!doc) return 1;
  int print = 0, clear_after = 0, set_ok = 1;
  for (int i = 3; i < argc; ++i) {
    if (!strcmp(argv[i], "--print")) { print = 1; continue; }
    if (!strcmp(argv[i], "--clear-after")) { clear_after = 1; continue; }
    int idx, st;
    if (sscanf(argv[i], "%d=%d", &idx, &st) == 2)
      set_ok &= FPDFDoc_SetOCGViewState(doc, idx, st);
  }
  if (clear_after) FPDFDoc_ClearOCGViewState(doc);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  int w = (int)FPDF_GetPageWidthF(page), h = (int)FPDF_GetPageHeightF(page);
  FPDF_BITMAP bmp = FPDFBitmap_Create(w, h, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, w, h, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, w, h, 0,
                        FPDF_ANNOT | (print ? FPDF_PRINTING : 0));
  char annot_px[32], content_px[32];
  Pixel(bmp, 350, h - 350, annot_px);
  Pixel(bmp, 100, h - 100, content_px);
  printf("{\"set_ok\":%d,\"state0\":%d,\"state1\":%d,\"annot\":%s,\"content\":%s}\n",
         set_ok, FPDFDoc_GetOCGViewState(doc, 0), FPDFDoc_GetOCGViewState(doc, 1),
         annot_px, content_px);
  FPDFBitmap_Destroy(bmp);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  return 0;
}

static int Persist(const char* in, const char* prefix) {
  size_t len = 0;
  unsigned char* orig = ReadAll(in, &len);
  FPDF_DOCUMENT doc = FPDF_LoadMemDocument64(orig, len, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);  // render state touches the page
  MemWriter full0, full1, incr;
  Save(doc, 0, &full0);
  int n = FPDFDoc_GetOCGCount(doc), set_ok = 1;
  for (int i = 0; i < n; ++i) set_ok &= FPDFDoc_SetOCGViewState(doc, i, 0);
  FPDF_BITMAP bmp = FPDFBitmap_Create(100, 100, 0);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, 100, 100, 0, FPDF_ANNOT);
  FPDFBitmap_Destroy(bmp);
  Save(doc, 0, &full1);
  MaskID(&full0);
  MaskID(&full1);
  memset(&incr, 0, sizeof incr);
  incr.fw.version = 1;
  incr.fw.WriteBlock = WriteBlockCb;
  int incr_ok = FPDF_SaveIncrementalObjects(doc, &incr.fw, NULL, 0);
  Buf b = {incr.data, incr.len};
  FPDF_FILEACCESS acc = {(unsigned long)incr.len, GetBlock, &b};
  uint32_t mismatch = 0;
  int verify = incr_ok && FPDF_VerifyIncrementalSave(doc, &acc, &mismatch);
  char path[4096];
  snprintf(path, sizeof path, "%s.full_with_override.pdf", prefix);
  WriteFile(path, &full1);
  printf("{\"set_ok\":%d,\"full_identical\":%d,\"incr_ok\":%d,"
         "\"incr_exact_copy\":%d,\"verify\":%d,\"mismatch\":%u}\n",
         set_ok, full0.len == full1.len && !memcmp(full0.data, full1.data, full0.len),
         incr_ok, incr.len == len && !memcmp(incr.data, orig, len), verify,
         mismatch);
  free(full0.data), free(full1.data), free(incr.data);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  free(orig);
  return 0;
}

static int Membership(const char* in, const char* prefix) {
  struct { const char* name; int idx[4]; int count; } cases[] = {
      {"one", {1}, 1},          {"two", {0, 1}, 2},
      {"dup", {1, 1, 0}, 3},    {"invalid", {0, 7}, 2},
      {"negative", {-1}, 1},    {"none", {0}, 0},
  };
  printf("{");
  for (size_t c = 0; c < sizeof cases / sizeof cases[0]; ++c) {
    FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
    FPDF_PAGE page = FPDF_LoadPage(doc, 0);
    FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, 0);
    int ret = FPDFAnnot_SetOCMembership(doc, a, cases[c].idx, cases[c].count);
    int index = FPDFAnnot_GetOCGIndex(doc, a);
    FPDFPage_CloseAnnot(a);
    FPDF_ClosePage(page);
    MemWriter w;
    Save(doc, 0, &w);
    char path[4096];
    snprintf(path, sizeof path, "%s.%s.pdf", prefix, cases[c].name);
    WriteFile(path, &w);
    free(w.data);
    FPDF_CloseDocument(doc);
    printf("%s\"%s\":{\"ret\":%d,\"ocg_index\":%d}", c ? "," : "",
           cases[c].name, ret, index);
  }
  printf("}\n");
  return 0;
}

static int Delete(int argc, char** argv) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[2], "");
  if (!doc) return 1;
  printf("{\"deleted\":[");
  for (int i = 4; i < argc; ++i)
    printf("%s%d", i > 4 ? "," : "", FPDFDoc_DeleteOCG(doc, atoi(argv[i])));
  printf("]}\n");
  MemWriter w;
  Save(doc, 0, &w);
  WriteFile(argv[3], &w);
  free(w.data);
  FPDF_CloseDocument(doc);
  return 0;
}

static int Unmark(const char* in, const char* out) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, 0);
  int ret = FPDFAnnot_SetAPOptionalContent(
      doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, NULL, 0);
  int bad_mode = FPDFAnnot_SetAPOptionalContent(
      doc, a, FPDF_ANNOT_APPEARANCEMODE_ROLLOVER, NULL, 0);
  int idx[1] = {9};
  int bad_index = FPDFAnnot_SetAPOptionalContent(
      doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, idx, 1);
  FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  MemWriter w;
  Save(doc, 0, &w);
  WriteFile(out, &w);
  free(w.data);
  FPDF_CloseDocument(doc);
  printf("{\"unmark\":%d,\"rollover\":%d,\"bad_index\":%d}\n", ret, bad_mode,
         bad_index);
  return 0;
}

static uint32_t Holder(FPDF_PAGE page) {
  uint32_t annots = FPDFPage_GetAnnotsObjectNumber(page);
  return annots ? annots : FPDFPage_GetObjectNumber(page);
}

static int AppendRect(FPDF_ANNOTATION a, unsigned g) {
  FPDF_PAGEOBJECT rect = FPDFPageObj_CreateNewRect(300, 300, 100, 100);
  return rect && FPDFPageObj_SetFillColor(rect, g ? 0 : 255, g, 0, 255) &&
         FPDFPath_SetDrawMode(rect, FPDF_FILLMODE_ALTERNATE, 0) &&
         FPDFAnnot_AppendObject(a, rect);
}

static int Incr(int argc, char** argv) {
  size_t len = 0;
  unsigned char* orig = ReadAll(argv[2], &len);
  FPDF_DOCUMENT doc = FPDF_LoadMemDocument64(orig, len, "");
  if (!doc) return 1;
  const char* scenario = argv[4];
  int idx[8], n = 0;
  for (int i = 5; i < argc && n < 8; ++i) idx[n++] = atoi(argv[i]);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  uint32_t holder = Holder(page);
  int ok = 1;
  FPDF_ANNOTATION a = NULL;
  if (!strcmp(scenario, "new")) {
    a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
    FS_RECTF r = {300, 400, 400, 300};
    ok = a && FPDFAnnot_SetRect(a, &r) &&
         FPDFAnnot_SetFlags(a, FPDF_ANNOT_FLAG_PRINT) && AppendRect(a, 0);
  } else {
    a = FPDFPage_GetAnnot(page, 0);
    if (!strcmp(scenario, "rebuilt")) {
      ok = a && FPDFAnnot_SetAP(a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, NULL) &&
           AppendRect(a, 255);
    } else if (!strcmp(scenario, "twice")) {
      int both[2] = {0, 1};
      ok = a && FPDFAnnot_SetAPOptionalContent(
                    doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, both, 2);
    }
  }
  int member = ok && FPDFAnnot_SetOCMembership(doc, a, idx, n);
  int marked = ok && FPDFAnnot_SetAPOptionalContent(
                         doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, idx, n);
  if (a) FPDFPage_CloseAnnot(a);
  MemWriter w;
  memset(&w, 0, sizeof w);
  w.fw.version = 1;
  w.fw.WriteBlock = WriteBlockCb;
  int saved = FPDF_SaveIncrementalObjects(doc, &w.fw, &holder, 1);
  Buf b = {w.data, w.len};
  FPDF_FILEACCESS acc = {(unsigned long)w.len, GetBlock, &b};
  uint32_t mismatch = 0;
  int verify = saved && FPDF_VerifyIncrementalSave(doc, &acc, &mismatch);
  if (saved) WriteFile(argv[3], &w);
  printf("{\"edit\":%d,\"membership\":%d,\"marked\":%d,\"saved\":%d,"
         "\"verify\":%d,\"mismatch\":%u,\"prefix\":%d}\n",
         ok, member, marked, saved, verify, mismatch,
         saved && w.len > len && !memcmp(w.data, orig, len));
  free(w.data);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  free(orig);
  return 0;
}

static FPDF_BOOL NoPause(IFSDK_PAUSE* p) { return 0; }

static void Sample(FPDF_BITMAP bmp, int h, const char* path, int first) {
  char a[32], c[32], wgt[32];
  Pixel(bmp, 350, h - 350, a);
  Pixel(bmp, 100, h - 100, c);
  Pixel(bmp, 150, h - 525, wgt);
  printf("%s\"%s\":{\"annot\":%s,\"content\":%s,\"widget\":%s}",
         first ? "" : ",", path, a, c, wgt);
}

static int Paths(int argc, char** argv) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[2], "");
  if (!doc) return 1;
  for (int i = 3; i < argc; ++i) {
    int idx, st;
    if (sscanf(argv[i], "%d=%d", &idx, &st) == 2)
      FPDFDoc_SetOCGViewState(doc, idx, st);
  }
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  int w = (int)FPDF_GetPageWidthF(page), h = (int)FPDF_GetPageHeightF(page);
  printf("{");
  const int lod = FPDF_RENDER_LOD_SKIP_SUBPIXEL;
  struct { const char* name; int progressive; int flags; } paths[] = {
      {"render", 0, FPDF_ANNOT},
      {"progressive", 1, FPDF_ANNOT},
      {"render_lod", 0, FPDF_ANNOT | lod},
      {"progressive_lod", 1, FPDF_ANNOT | lod},
  };
  for (size_t p = 0; p < sizeof paths / sizeof paths[0]; ++p) {
    FPDF_BITMAP bmp = FPDFBitmap_Create(w, h, 0);
    FPDFBitmap_FillRect(bmp, 0, 0, w, h, 0xFFFFFFFF);
    if (paths[p].progressive) {
      IFSDK_PAUSE pause = {1, NoPause, NULL};
      int st = FPDF_RenderPageBitmap_Start(bmp, page, 0, 0, w, h, 0,
                                           paths[p].flags, &pause);
      while (st == FPDF_RENDER_TOBECONTINUED)
        st = FPDF_RenderPage_Continue(page, &pause);
      FPDF_RenderPage_Close(page);
    } else {
      FPDF_RenderPageBitmap(bmp, page, 0, 0, w, h, 0, paths[p].flags);
    }
    Sample(bmp, h, paths[p].name, p == 0);
    FPDFBitmap_Destroy(bmp);
  }
  // FPDF_FFLDraw: page without annotations, then the form layer on top.
  static FPDF_FORMFILLINFO ffi;
  memset(&ffi, 0, sizeof ffi);
  ffi.version = 1;
  FPDF_FORMHANDLE form = FPDFDOC_InitFormFillEnvironment(doc, &ffi);
  FORM_OnAfterLoadPage(page, form);
  FPDF_BITMAP bmp = FPDFBitmap_Create(w, h, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, w, h, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, w, h, 0, 0);
  FPDF_FFLDraw(form, bmp, page, 0, 0, w, h, 0, 0);
  Sample(bmp, h, "ffldraw", 0);
  FPDFBitmap_Destroy(bmp);
  FORM_OnBeforeClosePage(page, form);
  FPDFDOC_ExitFormFillEnvironment(form);
  printf("}\n");
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 3) return 1;
  FPDF_InitLibrary();
  int rc = 1;
  if (!strcmp(argv[1], "make") && argc >= 4) rc = Make(argv[2], argv[3]);
  else if (!strcmp(argv[1], "render")) rc = Render(argc, argv);
  else if (!strcmp(argv[1], "persist") && argc >= 4) rc = Persist(argv[2], argv[3]);
  else if (!strcmp(argv[1], "membership") && argc >= 4) rc = Membership(argv[2], argv[3]);
  else if (!strcmp(argv[1], "delete") && argc >= 5) rc = Delete(argc, argv);
  else if (!strcmp(argv[1], "unmark") && argc >= 4) rc = Unmark(argv[2], argv[3]);
  else if (!strcmp(argv[1], "incr") && argc >= 5) rc = Incr(argc, argv);
  else if (!strcmp(argv[1], "paths")) rc = Paths(argc, argv);
  FPDF_DestroyLibrary();
  return rc;
}
