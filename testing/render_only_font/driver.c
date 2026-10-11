// Driver for form_render_only.patch (fork-p17, Rapida ra-4sj5g): rendering
// must never change the document. A FreeText without /AP whose text the /DA
// font cannot encode (CJK) is drawn through the fork_p8 render-only path; the
// font fallback (CPVT_FontMap::SetupAnnotSysPDFFont) must not add /DR fonts or
// objects to the document.
//
//   driver IN    save a copy (flags 0), render page 0 with FPDF_ANNOT, save
//                again; prints "same" when the two copies are byte-identical,
//                else "changed <before> <after>" (sizes).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fpdf_save.h"
#include "fpdfview.h"

typedef struct { FPDF_FILEWRITE base; unsigned char* buf; size_t len, cap; } MemWriter;
static int WriteBlock(FPDF_FILEWRITE* self, const void* data, unsigned long size) {
  MemWriter* w = (MemWriter*)self;
  if (w->len + size > w->cap) {
    w->cap = (w->len + size) * 2;
    w->buf = realloc(w->buf, w->cap);
    if (!w->buf) return 0;
  }
  memcpy(w->buf + w->len, data, size);
  w->len += size;
  return 1;
}
// The trailer's /ID: its second string is new on every save; blanked so two
// saves of an unchanged document compare equal.
static void BlankId(MemWriter* w) {
  for (size_t i = w->len; i-- > 3;) {
    if (memcmp(w->buf + i - 3, "/ID", 3)) continue;
    for (size_t j = i; j < w->len && w->buf[j] != ']'; ++j) w->buf[j] = ' ';
    return;
  }
}
static MemWriter Save(FPDF_DOCUMENT doc) {
  MemWriter w = {{1, WriteBlock}, NULL, 0, 0};
  if (!FPDF_SaveAsCopy(doc, &w.base, 0)) w.len = 0;
  BlankId(&w);
  return w;
}

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: driver IN\n"); return 2; }
  FPDF_InitLibrary();
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[1], NULL);
  if (!doc) { printf("error load\n"); return 1; }
  MemWriter before = Save(doc);
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  if (!page) { printf("error page\n"); return 1; }
  FPDF_BITMAP bmp = FPDFBitmap_Create(200, 200, 0);
  FPDFBitmap_FillRect(bmp, 0, 0, 200, 200, 0xFFFFFFFF);
  FPDF_RenderPageBitmap(bmp, page, 0, 0, 200, 200, 0, FPDF_ANNOT);
  // Drawn pixels: the markup was drawn (so the render-only path ran).
  const unsigned char* px = FPDFBitmap_GetBuffer(bmp);
  int stride = FPDFBitmap_GetStride(bmp), inked = 0;
  for (int y = 0; y < 200; ++y)
    for (int x = 0; x < 200; ++x)
      if (px[y * stride + x * 4] < 200) ++inked;
  fprintf(stderr, "inked %d\n", inked);
  FPDFBitmap_Destroy(bmp);
  FPDF_ClosePage(page);
  MemWriter after = Save(doc);
  if (!before.len || !after.len) { printf("error save\n"); return 1; }
  if (before.len == after.len && !memcmp(before.buf, after.buf, before.len)) printf("same\n");
  else {
    printf("changed %zu %zu\n", before.len, after.len);
    const char* dump = getenv("DRIVER_DUMP");   // a prefix: <prefix>.before.pdf / .after.pdf
    if (dump) {
      char path[1024];
      snprintf(path, sizeof path, "%s.before.pdf", dump);
      FILE* f = fopen(path, "wb");
      if (f) { fwrite(before.buf, 1, before.len, f); fclose(f); }
      snprintf(path, sizeof path, "%s.after.pdf", dump);
      f = fopen(path, "wb");
      if (f) { fwrite(after.buf, 1, after.len, f); fclose(f); }
    }
  }
  free(before.buf); free(after.buf);
  FPDF_CloseDocument(doc);
  FPDF_DestroyLibrary();
  return 0;
}
