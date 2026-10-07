// Driver for filter_unchanged.patch (FPDF_FilterUnchangedObjects).
//
//   driver SCENARIO IN [N]
//     revert   - annotation 0: /Rect and /C changed, then set back to the
//                original values; list = [annotation, page]
//     change   - annotation 0: /Rect changed; list = [annotation, page]
//     revert_direct - like revert, on annotation 1 (a direct dictionary in
//                the page's /Annots); list = [page]
//     unloaded - list = [N] (an object never loaded)
//     newobj   - FPDFDoc_CreateOCG; list = [new OCG] + the reported
//                modified objects
//   Prints the list before/after FPDF_FilterUnchangedObjects, whether the
//   document's object map and objects were unchanged by the call (stock
//   incremental + full save snapshots, /ID masked), then saves with the
//   filtered list (FPDF_SaveIncrementalObjects) and verifies; reports whether
//   the output is byte-identical to the original.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fpdf_annot.h"
#include "fpdf_doc.h"
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

static void Writer(MemWriter* w) {
  memset(w, 0, sizeof *w);
  w->fw.version = 1;
  w->fw.WriteBlock = WriteBlockCb;
}

static void Snapshot(FPDF_DOCUMENT doc, FPDF_DWORD flags, MemWriter* w) {
  Writer(w);
  FPDF_SaveAsCopy(doc, &w->fw, flags);
  for (size_t i = w->len; i-- > 3;) {
    if (!memcmp(w->data + i - 3, "/ID", 3)) {
      for (size_t j = i; j < w->len && w->data[j] != ']'; ++j) w->data[j] = '#';
      break;
    }
  }
}

static int Same(const MemWriter* a, const MemWriter* b) {
  return a->len == b->len && !memcmp(a->data, b->data, a->len);
}

typedef struct { const unsigned char* d; size_t n; } Buf;
static int GetBlock(void* p, unsigned long pos, unsigned char* out,
                    unsigned long sz) {
  Buf* b = (Buf*)p;
  if (pos + sz > b->n) return 0;
  memcpy(out, b->d + pos, sz);
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

int main(int argc, char** argv) {
  if (argc < 3) return 1;
  FPDF_InitLibrary();
  const char* scenario = argv[1];
  size_t len = 0;
  unsigned char* orig = ReadAll(argv[2], &len);
  FPDF_DOCUMENT doc = FPDF_LoadMemDocument64(orig, len, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  uint32_t list[8];
  int n = 0, edit_ok = 1;
  if (!strcmp(scenario, "revert") || !strcmp(scenario, "change") ||
      !strcmp(scenario, "revert_direct")) {
    FPDF_ANNOTATION a =
        FPDFPage_GetAnnot(page, !strcmp(scenario, "revert_direct") ? 1 : 0);
    FS_RECTF r0 = {0, 0, 0, 0}, moved;
    unsigned int R = 0, G = 0, B = 0, A = 255;
    edit_ok = a && FPDFAnnot_GetRect(a, &r0) &&
              FPDFAnnot_GetColor(a, FPDFANNOT_COLORTYPE_Color, &R, &G, &B, &A);
    moved = r0;
    moved.left += 7;
    moved.right += 7;
    edit_ok = edit_ok && FPDFAnnot_SetRect(a, &moved);
    if (strcmp(scenario, "change")) {
      edit_ok = edit_ok && FPDFAnnot_SetRect(a, &r0) &&
                FPDFAnnot_SetColor(a, FPDFANNOT_COLORTYPE_Color, 0, 0, 255, A) &&
                FPDFAnnot_SetColor(a, FPDFANNOT_COLORTYPE_Color, R, G, B, A);
    }
    if (FPDFAnnot_GetObjectNumber(a)) list[n++] = FPDFAnnot_GetObjectNumber(a);
    list[n++] = FPDFPage_GetObjectNumber(page);
    if (a) FPDFPage_CloseAnnot(a);
  } else if (!strcmp(scenario, "unloaded") && argc >= 4) {
    list[n++] = (uint32_t)atoi(argv[3]);
  } else if (!strcmp(scenario, "newobj")) {
    FPDF_WCHAR name[4] = {'N', 'e', 'w', 0};
    int idx = FPDFDoc_CreateOCG(doc, name);
    edit_ok = idx >= 0;
    list[n++] = (uint32_t)FPDFDoc_GetOCGObjectNumber(doc, idx);
    // plus the existing objects the call modified (the catalog here)
    uint32_t mod[4];
    int m = FPDFDoc_GetLastModifiedObjects(doc, mod, 4);
    for (int i = 0; i < m && n < 8; ++i) list[n++] = mod[i];
  }
  printf("{\"edit\":%d,\"before\":[", edit_ok);
  for (int i = 0; i < n; ++i) printf("%s%u", i ? "," : "", list[i]);
  // Object map: stock incremental saves (every map object) around the call.
  // Object contents: full saves around a second call on a copy of the list
  // (a stock full save itself loads objects into the map, so it must not
  // sit between the map snapshots).
  uint32_t copy[8];
  memcpy(copy, list, sizeof list);
  MemWriter m0, f0, m1, f1;
  Snapshot(doc, FPDF_INCREMENTAL, &m0);
  int kept = FPDF_FilterUnchangedObjects(doc, list, n);
  Snapshot(doc, FPDF_INCREMENTAL, &m1);
  Snapshot(doc, 0, &f0);
  FPDF_FilterUnchangedObjects(doc, copy, n);
  Snapshot(doc, 0, &f1);
  printf("],\"after\":[");
  for (int i = 0; i < kept; ++i) printf("%s%u", i ? "," : "", list[i]);
  MemWriter w;
  Writer(&w);
  int saved = FPDF_SaveIncrementalObjects(doc, &w.fw, kept ? list : NULL,
                                          kept > 0 ? kept : 0);
  Buf b = {w.data, w.len};
  FPDF_FILEACCESS acc = {(unsigned long)w.len, GetBlock, &b};
  uint32_t mismatch = 0;
  int verify = saved && FPDF_VerifyIncrementalSave(doc, &acc, &mismatch);
  printf("],\"kept\":%d,\"map_unchanged\":%d,\"objects_unchanged\":%d,"
         "\"saved\":%d,\"verify\":%d,\"identical_to_original\":%d}\n",
         kept, Same(&m0, &m1), Same(&f0, &f1), saved, verify,
         saved && w.len == len && !memcmp(w.data, orig, len));
  free(m0.data), free(f0.data), free(m1.data), free(f1.data), free(w.data);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  free(orig);
  FPDF_DestroyLibrary();
  return 0;
}
