// Driver for fork_p17.patch: FPDF_SaveIncrementalObjects must not refuse a
// document because a LOADED object holds a dangling reference (an object
// number that was never used, above the file's last — e.g. a broken outline
// /Dest [999 0 R /Fit]). Rapida ra-n1j65.3 P1 / NAV-05.6a.
//
//   driver IN walk|nowalk OBJNUM
//     walk:   read the outline first (as a Bookmarks panel does: titles and
//             FPDFDest_GetDestPageIndex), which loads the outline items;
//     nowalk: don't.
//     Then save incrementally (to memory) listing OBJNUM; prints "ok" or
//     "refused".
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fpdf_doc.h"
#include "fpdf_save.h"
#include "fpdfview.h"

typedef struct { FPDF_FILEWRITE base; size_t len; } CountWriter;
static int WriteBlock(FPDF_FILEWRITE* self, const void* data, unsigned long size) {
  (void)data;
  ((CountWriter*)self)->len += size;
  return 1;
}

static void Walk(FPDF_DOCUMENT doc, FPDF_BOOKMARK bm, int depth) {
  for (; bm && depth < 32; bm = FPDFBookmark_GetNextSibling(doc, bm)) {
    unsigned short title[256];
    FPDFBookmark_GetTitle(bm, title, sizeof title);
    FPDF_DEST dest = FPDFBookmark_GetDest(doc, bm);
    if (dest) FPDFDest_GetDestPageIndex(doc, dest);
    Walk(doc, FPDFBookmark_GetFirstChild(doc, bm), depth + 1);
  }
}

int main(int argc, char** argv) {
  if (argc < 4) { fprintf(stderr, "usage: driver IN walk|nowalk OBJNUM\n"); return 2; }
  FPDF_InitLibrary();
  FPDF_DOCUMENT doc = FPDF_LoadDocument(argv[1], NULL);
  if (!doc) { printf("error load\n"); return 1; }
  if (!strcmp(argv[2], "walk")) Walk(doc, FPDFBookmark_GetFirstChild(doc, NULL), 0);
  uint32_t objnum = (uint32_t)strtoul(argv[3], NULL, 10);
  CountWriter w = {{1, WriteBlock}, 0};
  printf("%s\n", FPDF_SaveIncrementalObjects(doc, &w.base, &objnum, 1) ? "ok" : "refused");
  FPDF_CloseDocument(doc);
  FPDF_DestroyLibrary();
  return 0;
}
