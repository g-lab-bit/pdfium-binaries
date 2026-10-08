// Driver for annot_undo_wrap.patch (FPDFAnnot_SaveState / RestoreState /
// FPDFPage_InsertAnnotState / ReleaseState, empty /Annots = none in
// FPDF_FilterUnchangedObjects) and annot_undo_wrap.patch (FPDFAnnot_WrapAppearance).
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

static int Restore(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a0 = FPDFPage_GetAnnot(page, 0), a1 = FPDFPage_GetAnnot(page, 1);
  int s0 = FPDFAnnot_SaveState(doc, a0), s1 = FPDFAnnot_SaveState(doc, a1);
  FPDFAnnot_SetColor(a0, FPDFANNOT_COLORTYPE_Color, 0, 0, 255, 255);
  FPDFAnnot_SetColor(a1, FPDFANNOT_COLORTYPE_Color, 0, 0, 255, 255);
  FPDFAnnot_SetStringValue(a0, "Contents", (FPDF_WIDESTRING) "x\0\0");
  int changed = Kept(doc, 10, 3);
  int r0 = FPDFAnnot_RestoreState(doc, a0, s0), r1 = FPDFAnnot_RestoreState(doc, a1, s1);
  int after = Kept(doc, 10, 3);
  int wrong = FPDFAnnot_RestoreState(doc, a0, s1);   // a direct state on an indirect annotation
  int bad = FPDFAnnot_RestoreState(doc, a0, 999);
  int again = FPDFAnnot_RestoreState(doc, a0, s0);   // the state stays
  FPDFAnnot_ReleaseState(doc, s0);
  int released = FPDFAnnot_RestoreState(doc, a0, s0);
  printf("{\"ids\":[%d,%d],\"changed\":%d,\"restored\":[%d,%d],\"after\":%d,\"wrong\":%d,\"bad\":%d,\"again\":%d,\"released\":%d}\n",
         s0, s1, changed, r0, r1, after, wrong, bad, again, released);
  FPDFPage_CloseAnnot(a0); FPDFPage_CloseAnnot(a1);
  FPDF_ClosePage(page); FPDF_CloseDocument(doc);
  return 0;
}

static int Reinsert(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, NULL);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION a0 = FPDFPage_GetAnnot(page, 0), a1 = FPDFPage_GetAnnot(page, 1);
  int s0 = FPDFAnnot_SaveState(doc, a0), s1 = FPDFAnnot_SaveState(doc, a1);
  FPDFPage_CloseAnnot(a0); FPDFPage_CloseAnnot(a1);
  FPDFPage_RemoveAnnot(page, 1);
  FPDFPage_RemoveAnnot(page, 0);
  int removed = Kept(doc, 3, 0);
  int i0 = FPDFPage_InsertAnnotState(doc, page, s0, 0);
  int i1 = FPDFPage_InsertAnnotState(doc, page, s1, 1);
  int twice = FPDFPage_InsertAnnotState(doc, page, s0, 0);   // already on the page
  int after = Kept(doc, 3, 10);
  FPDF_ANNOTATION b0 = FPDFPage_GetAnnot(page, 0);
  unsigned long obj0 = FPDFAnnot_GetObjectNumber(b0);
  FPDFPage_CloseAnnot(b0);
  int count = FPDFPage_GetAnnotCount(page);
  printf("{\"removed\":%d,\"inserted\":[%d,%d],\"twice\":%d,\"after\":%d,\"obj0\":%lu,\"count\":%d}\n",
         removed, i0, i1, twice, after, obj0, count);
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

// BGRA at the annotation centre (150, 125), 72 dpi, with FPDF_ANNOT.
static void Pixel(FPDF_DOCUMENT doc, int out[3]) {
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  int w = 300, h = 300;
  FPDF_BITMAP bmp = FPDFBitmap_Create(w, h, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, w, h, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, w, h, 0, FPDF_ANNOT);
  const unsigned char* p = (const unsigned char*)FPDFBitmap_GetBuffer(bmp) +
                           (h - 125) * FPDFBitmap_GetStride(bmp) + 150 * 4;
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
  else if (!strcmp(mode, "rewrap"))
    ok = FPDFAnnot_WrapAppearance(doc, a, blue, NULL, 0) && FPDFAnnot_WrapAppearance(doc, a, blue, layers, 1);
  const float bad_rgb[3] = {2.0f, 0, 0};
  const int bad_layer[1] = {9};
  int bad1 = FPDFAnnot_WrapAppearance(doc, a, bad_rgb, NULL, 0);
  int bad2 = FPDFAnnot_WrapAppearance(doc, a, NULL, bad_layer, 1);
  FPDFPage_CloseAnnot(a);
  FPDF_ClosePage(page);
  int on[3], off[3];
  Pixel(doc, on);
  FPDFDoc_SetOCGViewState(doc, 0, 0);
  Pixel(doc, off);
  FPDFDoc_SetOCGViewState(doc, 0, -1);
  int saved = SaveTo(doc, out);
  printf("{\"ok\":%d,\"bad_rgb\":%d,\"bad_layer\":%d,\"on\":[%d,%d,%d],\"off\":[%d,%d,%d],\"saved\":%d}\n",
         ok, bad1, bad2, on[0], on[1], on[2], off[0], off[1], off[2], saved);
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
  FPDF_DestroyLibrary();
  return rc;
}
