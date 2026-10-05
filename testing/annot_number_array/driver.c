// Driver for FPDFAnnot_GetNumberArray (annot_number_array.patch).
// usage: driver FIXTURE.pdf
// Prints one JSON object per line: {"case":..., "ret":..., "values":[...]}
// plus {"case":"getcolor_with_ap", "ok":0|1} and
// {"case":"nomutate", "equal":0|1}.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fpdf_annot.h"
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

// Stock incremental save: writes every object in the document's map. The
// trailer /ID (random second element) is masked.
static void StockSave(FPDF_DOCUMENT doc, MemWriter* w) {
  memset(w, 0, sizeof *w);
  w->fw.version = 1;
  w->fw.WriteBlock = WriteBlockCb;
  FPDF_SaveAsCopy(doc, &w->fw, FPDF_INCREMENTAL);
  for (size_t i = w->len; i-- > 3;) {
    if (!memcmp(w->data + i - 3, "/ID", 3)) {
      for (size_t j = i; j < w->len && w->data[j] != ']'; ++j) w->data[j] = '#';
      break;
    }
  }
}

static void Case(FPDF_PAGE page, const char* name, int index, const char* key,
                 int use_buffer, unsigned long count) {
  float buf[8];
  for (int i = 0; i < 8; ++i) buf[i] = -99.0f;  // sentinel
  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, index);
  int ret = FPDFAnnot_GetNumberArray(a, key, use_buffer ? buf : NULL, count);
  FPDFPage_CloseAnnot(a);
  printf("{\"case\":\"%s\",\"ret\":%d,\"values\":[", name, ret);
  for (int i = 0; i < 4; ++i) printf("%s%g", i ? "," : "", buf[i]);
  printf("]}\n");
}

int main(int argc, char** argv) {
  if (argc < 2) return 1;
  FPDF_InitLibrary();
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[1], "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  if (!page) return 1;
  // FPDFPage_GetAnnot loads the annotation dictionaries; do that before the
  // first snapshot so the check isolates FPDFAnnot_GetNumberArray.
  for (int i = 0; i < FPDFPage_GetAnnotCount(page); ++i)
    FPDFPage_CloseAnnot(FPDFPage_GetAnnot(page, i));
  MemWriter before, after;
  StockSave(doc, &before);

  Case(page, "c_with_ap", 0, "C", 1, 8);
  Case(page, "c_no_ap", 1, "C", 1, 8);
  Case(page, "ic_absent", 0, "IC", 1, 8);
  Case(page, "not_an_array", 0, "F", 1, 8);
  Case(page, "indirect_array", 2, "C", 1, 8);
  Case(page, "indirect_element", 2, "IC", 1, 8);
  Case(page, "mixed_types", 3, "C", 1, 8);
  Case(page, "empty_array", 4, "C", 1, 8);
  Case(page, "size_query_null", 0, "C", 0, 8);
  Case(page, "size_query_count0", 0, "C", 1, 0);
  Case(page, "truncated_copy", 0, "C", 1, 2);
  Case(page, "null_key", 0, NULL, 1, 8);

  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, 0);
  unsigned int r, g, b, al;
  printf("{\"case\":\"getcolor_with_ap\",\"ok\":%d}\n",
         FPDFAnnot_GetColor(a, FPDFANNOT_COLORTYPE_Color, &r, &g, &b, &al));
  FPDFPage_CloseAnnot(a);

  StockSave(doc, &after);
  printf("{\"case\":\"nomutate\",\"equal\":%d}\n",
         before.len == after.len && !memcmp(before.data, after.data, before.len));
  free(before.data);
  free(after.data);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  FPDF_DestroyLibrary();
  return 0;
}
