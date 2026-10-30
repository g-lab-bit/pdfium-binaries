// Driver for fork_p18.patch (Rapida ra-1okhu, ra-c2kyq.12, ra-4sj5g.1).
//
//   driver IN
// Prints one "key value" line per check; run_tests.py compares them.
//   version            FPDF_GetForkVersion()
//   square.shown       RGB at the square's centre, rendered with FPDF_ANNOT
//   square.hidden      ... after FPDFAnnot_SetRenderHidden(.., true)
//   square.again       ... after FPDFAnnot_SetRenderHidden(.., false)
//   square.cleared     ... hidden again, then FPDFDoc_ClearRenderHiddenAnnots
//   (IN.before.pdf / IN.during.pdf: saves without and while hidden; run_tests.py
//   compares them, the trailer /ID aside, which is random per save)
//   widget.plain       RGB of the widget's non-OC bar under FPDF_FFLDraw
//   widget.oc.default  RGB of its OC-marked area: the OCG is /OFF in /D
//   widget.oc.on       ... after FPDFDoc_SetOCGViewState(doc, 0, 1)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fpdf_annot.h"
#include "fpdf_doc.h"
#include "fpdf_formfill.h"
#include "fpdf_save.h"
#include "fpdfview.h"

#define SIZE 200

typedef struct { FPDF_FILEWRITE base; unsigned char* buf; size_t len, cap; } MemWriter;
static int WriteBlock(FPDF_FILEWRITE* self, const void* data, unsigned long size) {
  MemWriter* w = (MemWriter*)self;
  if (w->len + size > w->cap) {
    w->cap = (w->len + size) * 2;
    w->buf = realloc(w->buf, w->cap);
  }
  memcpy(w->buf + w->len, data, size);
  w->len += size;
  return 1;
}

static void Save(FPDF_DOCUMENT doc, MemWriter* w, const char* in, const char* suffix) {
  memset(w, 0, sizeof *w);
  w->base.version = 1;
  w->base.WriteBlock = WriteBlock;
  FPDF_SaveAsCopy(doc, &w->base, FPDF_NO_INCREMENTAL);
  char path[1024];
  snprintf(path, sizeof path, "%s.%s.pdf", in, suffix);
  FILE* f = fopen(path, "wb");
  if (f) { fwrite(w->buf, 1, w->len, f); fclose(f); }
}

static void Pixel(FPDF_BITMAP bmp, int x, int y, const char* key) {
  const unsigned char* p = (const unsigned char*)FPDFBitmap_GetBuffer(bmp) +
                           y * FPDFBitmap_GetStride(bmp) + x * 4;
  printf("%s %d,%d,%d\n", key, p[2], p[1], p[0]);  // BGRA
}

// Page point (x, y) -> bitmap pixel, at scale 1 and no rotation.
static void PagePixel(FPDF_DOCUMENT doc, FPDF_PAGE page, double x, double y, const char* key) {
  (void)doc;
  FPDF_BITMAP bmp = FPDFBitmap_Create(SIZE, SIZE, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, SIZE, SIZE, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, SIZE, SIZE, 0, FPDF_ANNOT);
  Pixel(bmp, (int)x, SIZE - (int)y, key);
  FPDFBitmap_Destroy(bmp);
}

static void FormPixel(FPDF_FORMHANDLE form, FPDF_PAGE page, double x, double y, const char* key) {
  FPDF_BITMAP bmp = FPDFBitmap_Create(SIZE, SIZE, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, SIZE, SIZE, 0xFFFFFFFF);
  FPDF_FFLDraw(form, bmp, page, 0, 0, SIZE, SIZE, 0, 0);
  Pixel(bmp, (int)x, SIZE - (int)y, key);
  FPDFBitmap_Destroy(bmp);
}

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: driver IN\n"); return 2; }
  FPDF_InitLibrary();
  printf("version %s\n", FPDF_GetForkVersion());

  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[1], NULL);
  if (!doc) { printf("error load\n"); return 1; }
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  FPDF_ANNOTATION square = NULL;
  for (int i = 0; i < FPDFPage_GetAnnotCount(page); ++i) {
    FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, i);
    if (FPDFAnnot_GetSubtype(a) == FPDF_ANNOT_SQUARE) { square = a; break; }
    FPDFPage_CloseAnnot(a);
  }
  if (!square) { printf("error square\n"); return 1; }

  // ra-c2kyq.12: render-only hide.
  PagePixel(doc, page, 50, 50, "square.shown");
  MemWriter before, during;
  Save(doc, &before, argv[1], "before");
  printf("hide.ok %d\n", FPDFAnnot_SetRenderHidden(doc, square, 1));
  PagePixel(doc, page, 50, 50, "square.hidden");
  Save(doc, &during, argv[1], "during");
  FPDFAnnot_SetRenderHidden(doc, square, 0);
  PagePixel(doc, page, 50, 50, "square.again");
  FPDFAnnot_SetRenderHidden(doc, square, 1);
  FPDFDoc_ClearRenderHiddenAnnots(doc);
  PagePixel(doc, page, 50, 50, "square.cleared");
  printf("hide.badargs %d\n", FPDFAnnot_SetRenderHidden(NULL, square, 1));
  FPDFPage_CloseAnnot(square);

  // ra-4sj5g.1: optional content inside a widget's appearance under FFLDraw.
  FPDF_FORMFILLINFO info;
  memset(&info, 0, sizeof info);
  info.version = 1;
  FPDF_FORMHANDLE form = FPDFDOC_InitFormFillEnvironment(doc, &info);
  FORM_OnAfterLoadPage(page, form);
  FormPixel(form, page, 150, 125, "widget.plain");
  FormPixel(form, page, 150, 160, "widget.oc.default");
  FPDFDoc_SetOCGViewState(doc, 0, 1);
  FormPixel(form, page, 150, 160, "widget.oc.on");
  FORM_OnBeforeClosePage(page, form);
  FPDFDOC_ExitFormFillEnvironment(form);

  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  FPDF_DestroyLibrary();
  free(before.buf);
  free(during.buf);
  return 0;
}
