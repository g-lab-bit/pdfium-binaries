// Driver for annot_undo_wrap.patch: FPDFAnnot_SaveState / RestoreState /
// FPDFPage_InsertAnnotState / ReleaseState, empty /Annots = none in
// FPDF_FilterUnchangedObjects, and FPDFAnnot_WrapAppearance.
//
//   driver restore IN        edit annotation 0 (indirect) and 1 (direct), restore
//                            both; prints what FPDF_FilterUnchangedObjects keeps.
//   driver reinsert IN       save states, remove both annotations, insert them
//                            back at their positions; prints the filter result
//                            and object numbers.
//   driver empty IN          (page without /Annots) create + remove an
//                            annotation; prints the filter result for the page.
//   driver wrap IN OUT MODE  annotation 0 (a stamp with an /AP form) wrapped:
//                            MODE tint | layer | both | unwrap | rewrap;
//                            prints pixels (annotation centre) with the layer on
//                            and off; full save to OUT.
//   driver color IN OUT SEQ  (fork-p14) annotation 0 wrapped once per letter of
//                            SEQ: b / g = blue / green tint, B = blue tint +
//                            layer, L = layer only, U = unwrap, x = a refused
//                            call (bad layer); prints the tint
//                            FPDFAnnot_GetAppearanceTint reports; save to OUT.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fpdf_annot.h"
#include "fpdf_doc.h"
#include "fpdf_edit.h"
#include "fpdf_save.h"
#include "fpdfview.h"

typedef struct { FPDF_FILEWRITE base; FILE* fp; } FileWriter;
static int WriteBlock(FPDF_FILEWRITE* self, const void* data, unsigned long size) {
  return fwrite(data, 1, size, ((FileWriter*)self)->fp) == size;
}
static int SaveTo(FPDF_DOCUMENT doc, const char* path) {
  FileWriter w = {{1, WriteBlock}, fopen(path, "wb")};
  if (!w.fp) return 0;
  int ok = FPDF_SaveAsCopy(doc, &w.base, 0);
  fclose(w.fp);
  return ok;
}
// Number of |objnums| FPDF_FilterUnchangedObjects keeps (changed).
static int Kept(FPDF_DOCUMENT doc, uint32_t a, uint32_t b) {
  uint32_t list[2] = {a, b};
  return FPDF_FilterUnchangedObjects(doc, list, b ? 2 : 1);
}

static int Modified(FPDF_DOCUMENT doc, uint32_t objnum) {
  uint32_t buf[16];
  int n = FPDFDoc_GetLastModifiedObjects(doc, buf, 16);
  for (int i = 0; i < n && i < 16; ++i) if (buf[i] == objnum) return 1;
  return 0;
}

static int Restore(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a0 = FPDFPage_GetAnnot(page, 0), a1 = FPDFPage_GetAnnot(page, 1);
  FPDF_ANNOTATION a2 = FPDFPage_GetAnnot(page, 2);
  int s0 = FPDFAnnot_SaveState(doc, a0), s1 = FPDFAnnot_SaveState(doc, a1);
  FPDFAnnot_SetColor(a0, FPDFANNOT_COLORTYPE_Color, 0, 0, 255, 255);
  FPDFAnnot_SetColor(a1, FPDFANNOT_COLORTYPE_Color, 0, 0, 255, 255);
  FPDFAnnot_SetStringValue(a0, "Contents", (FPDF_WIDESTRING) "x\0\0");
  int changed = Kept(doc, 10, 3);
  int r0 = FPDFAnnot_RestoreState(doc, a0, s0);
  int reported = Modified(doc, 10);
  int r1 = FPDFAnnot_RestoreState(doc, a1, s1);
  int after = Kept(doc, 10, 3);
  int wrong_direct = FPDFAnnot_RestoreState(doc, a2, s1);   // another direct annotation
  int wrong = FPDFAnnot_RestoreState(doc, a0, s1);   // a direct state on an indirect annotation
  int bad = FPDFAnnot_RestoreState(doc, a0, 999);
  int again = FPDFAnnot_RestoreState(doc, a0, s0);   // the state stays
  FPDFAnnot_ReleaseState(doc, s0);
  int released = FPDFAnnot_RestoreState(doc, a0, s0);
  printf("{\"ids\":[%d,%d],\"changed\":%d,\"restored\":[%d,%d],\"after\":%d,\"wrong\":%d,\"bad\":%d,\"again\":%d,\"released\":%d}\n",
         s0, s1, changed, r0, r1, after, wrong, bad, again, released);
  printf("{\"reported\":%d,\"wrong_direct\":%d}\n", reported, wrong_direct);
  FPDFPage_CloseAnnot(a0); FPDFPage_CloseAnnot(a1); FPDFPage_CloseAnnot(a2);
  FPDF_ClosePage(page); FPDF_CloseDocument(doc);
  return 0;
}

static int Reinsert(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a0 = FPDFPage_GetAnnot(page, 0), a1 = FPDFPage_GetAnnot(page, 1);
  int older = FPDFAnnot_SaveState(doc, a1);   // an earlier undo step of the direct one
  FPDFAnnot_SetColor(a1, FPDFANNOT_COLORTYPE_Color, 9, 9, 9, 255);
  int s0 = FPDFAnnot_SaveState(doc, a0), s1 = FPDFAnnot_SaveState(doc, a1);
  FPDFPage_CloseAnnot(a0); FPDFPage_CloseAnnot(a1);
  FPDFPage_RemoveAnnot(page, 1);
  FPDFPage_RemoveAnnot(page, 0);
  int removed = Kept(doc, 3, 0);
  int i0 = FPDFPage_InsertAnnotState(doc, page, s0, 0);
  int reported = Modified(doc, 3);   // the page (its /Annots); annotation 10 itself is unchanged
  int i1 = FPDFPage_InsertAnnotState(doc, page, s1, 1);
  int twice = FPDFPage_InsertAnnotState(doc, page, s0, 0);   // already on the page
  int twice_direct = FPDFPage_InsertAnnotState(doc, page, s1, 1);   // put back once only
  FPDF_ANNOTATION back1 = FPDFPage_GetAnnot(page, 1);
  int older_ok = FPDFAnnot_RestoreState(doc, back1, older);   // still the same dictionary
  FPDFPage_CloseAnnot(back1);
  int after = Kept(doc, 3, 10);
  FPDF_ANNOTATION b0 = FPDFPage_GetAnnot(page, 0);
  unsigned long obj0 = FPDFAnnot_GetObjectNumber(b0);
  FPDFPage_CloseAnnot(b0);
  int count = FPDFPage_GetAnnotCount(page);
  printf("{\"removed\":%d,\"inserted\":[%d,%d],\"twice\":%d,\"twice_direct\":%d,\"after\":%d,\"obj0\":%lu,\"count\":%d,\"reported\":%d,\"older_ok\":%d}\n",
         removed, i0, i1, twice, twice_direct, after, obj0, count, reported, older_ok);
  FPDF_ClosePage(page); FPDF_CloseDocument(doc);
  return 0;
}

static int Empty(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_SQUARE);
  FPDFPage_CloseAnnot(a);
  int with = Kept(doc, 3, 0);
  FPDFPage_RemoveAnnot(page, 0);
  int without = Kept(doc, 3, 0);
  printf("{\"with_annot\":%d,\"after_remove\":%d}\n", with, without);
  FPDF_ClosePage(page); FPDF_CloseDocument(doc);
  return 0;
}

// RGB at page point (x, y), 72 dpi, with FPDF_ANNOT.
static void PixelAt(FPDF_DOCUMENT doc, int x, int y, int out[3]) {
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  int w = 300, h = 300;
  FPDF_BITMAP bmp = FPDFBitmap_Create(w, h, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, w, h, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, w, h, 0, FPDF_ANNOT);
  const unsigned char* p = (const unsigned char*)FPDFBitmap_GetBuffer(bmp) +
                           (h - y) * FPDFBitmap_GetStride(bmp) + x * 4;
  out[0] = p[2]; out[1] = p[1]; out[2] = p[0];
  FPDFBitmap_Destroy(bmp);
  FPDF_ClosePage(page);
}

static int Wrap(const char* in, const char* out, const char* mode) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, 0);
  const float blue[3] = {0.0f, 0.3f, 1.0f};
  const int layers[1] = {0};
  int ok = 0;
  if (!strcmp(mode, "tint")) ok = FPDFAnnot_WrapAppearance(doc, a, blue, NULL, 0);
  else if (!strcmp(mode, "layer")) ok = FPDFAnnot_WrapAppearance(doc, a, NULL, layers, 1);
  else if (!strcmp(mode, "both")) ok = FPDFAnnot_WrapAppearance(doc, a, blue, layers, 1);
  else if (!strcmp(mode, "unwrap"))
    ok = FPDFAnnot_WrapAppearance(doc, a, blue, layers, 1) && FPDFAnnot_WrapAppearance(doc, a, NULL, NULL, 0);
  else if (!strcmp(mode, "noop")) {   // never wrapped: no change at all
    ok = FPDFAnnot_WrapAppearance(doc, a, NULL, NULL, 0);
    printf("{\"noop_kept\":%d}\n", Kept(doc, 10, 0));
  }
  else if (!strcmp(mode, "rewrap"))
    ok = FPDFAnnot_WrapAppearance(doc, a, blue, NULL, 0) && FPDFAnnot_WrapAppearance(doc, a, blue, layers, 1);
  const float bad_rgb[3] = {2.0f, 0, 0};
  const int bad_layer[1] = {9};
  int bad1 = FPDFAnnot_WrapAppearance(doc, a, bad_rgb, NULL, 0);
  int bad2 = FPDFAnnot_WrapAppearance(doc, a, NULL, bad_layer, 1);
  FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  int on[3], off[3], left[3], right[3];
  PixelAt(doc, 150, 125, on);
  PixelAt(doc, 125, 125, left);
  PixelAt(doc, 175, 125, right);
  FPDFDoc_SetOCGViewState(doc, 0, 0);
  PixelAt(doc, 150, 125, off);
  FPDFDoc_SetOCGViewState(doc, 0, -1);
  int saved = SaveTo(doc, out);
  printf("{\"ok\":%d,\"bad_rgb\":%d,\"bad_layer\":%d,\"on\":[%d,%d,%d],\"off\":[%d,%d,%d],"
         "\"left\":[%d,%d,%d],\"right\":[%d,%d,%d],\"saved\":%d}\n",
         ok, bad1, bad2, on[0], on[1], on[2], off[0], off[1], off[2],
         left[0], left[1], left[2], right[0], right[1], right[2], saved);
  FPDF_CloseDocument(doc);
  return 0;
}

static int Color(const char* in, const char* out, const char* seq) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, 0);
  const float blue[3] = {0.0f, 0.25f, 1.0f}, green[3] = {0.0f, 0.5f, 0.0f};
  const int layers[1] = {0};
  float before[3] = {-1, -1, -1};
  int tinted_before = FPDFAnnot_GetAppearanceTint(a, before);
  const int bad_layer[1] = {9};
  int ok = 1, refused_did = 0;
  for (const char* c = seq; *c; ++c) {
    if (*c == 'b') ok &= FPDFAnnot_WrapAppearance(doc, a, blue, NULL, 0);
    else if (*c == 'g') ok &= FPDFAnnot_WrapAppearance(doc, a, green, NULL, 0);
    else if (*c == 'B') ok &= FPDFAnnot_WrapAppearance(doc, a, blue, layers, 1);
    else if (*c == 'L') ok &= FPDFAnnot_WrapAppearance(doc, a, NULL, layers, 1);
    else if (*c == 'U') ok &= FPDFAnnot_WrapAppearance(doc, a, NULL, NULL, 0);
    else if (*c == 'x') refused_did |= FPDFAnnot_WrapAppearance(doc, a, green, bad_layer, 1);
  }
  float t[3] = {-1, -1, -1};
  int tinted = FPDFAnnot_GetAppearanceTint(a, t);
  int null_out = FPDFAnnot_GetAppearanceTint(a, NULL);
  FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  int saved = SaveTo(doc, out);
  printf("{\"ok\":%d,\"refused_did\":%d,\"tinted_before\":%d,\"tinted\":%d,\"tint\":[%.3f,%.3f,%.3f],"
         "\"null_out\":%d,\"saved\":%d}\n",
         ok, refused_did, tinted_before, tinted, t[0], t[1], t[2], null_out, saved);
  FPDF_CloseDocument(doc);
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 3) return 2;
  FPDF_InitLibrary();
  int rc = 2;
  if (!strcmp(argv[1], "restore")) rc = Restore(argv[2]);
  else if (!strcmp(argv[1], "reinsert")) rc = Reinsert(argv[2]);
  else if (!strcmp(argv[1], "empty")) rc = Empty(argv[2]);
  else if (!strcmp(argv[1], "wrap") && argc >= 5) rc = Wrap(argv[2], argv[3], argv[4]);
  else if (!strcmp(argv[1], "color") && argc >= 5) rc = Color(argv[2], argv[3], argv[4]);
  FPDF_DestroyLibrary();
  return rc;
}
