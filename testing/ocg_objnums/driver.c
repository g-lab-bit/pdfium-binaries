// Driver for ocg_objnums.patch and verify_empty_stream.patch.
//
//   driver ids IN
//       Prints FPDFDoc_GetCatalogObjectNumber and
//       FPDFDoc_GetOCPropertiesObjectNumber.
//   driver op IN OPERATION
//       Runs one fork write call on a freshly loaded document, reads
//       FPDFDoc_GetLastModifiedObjects, then (on the same open document):
//         - FPDF_SaveIncrementalObjects listing exactly those objects, and
//           FPDF_VerifyIncrementalSave;
//         - for every reported object, the same save with that object left
//           out (must be refused, or verify FALSE).
//       Annotation 0 = indirect Square with an OCMD and an existing /AP;
//       annotation 1 = direct Square on L. OCG 0 = WS, 1 = L.
//   driver empty IN MODE
//       new:    adds a Text annotation whose /AP /N is an EMPTY stream
//               (FPDFAnnot_SetAP with ""), lists the /Annots holder;
//       listed: lists the existing empty stream object given by the fixture
//               (annotation 0's /AP /D) unchanged.
//       Then FPDF_SaveIncrementalObjects + FPDF_VerifyIncrementalSave.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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

// 1 = saved and verified, 0 = verify FALSE, -1 = save refused.
static int SaveVerify(FPDF_DOCUMENT doc, const uint32_t* list, int n,
                      uint32_t* mismatch) {
  MemWriter w;
  memset(&w, 0, sizeof w);
  w.fw.version = 1;
  w.fw.WriteBlock = WriteBlockCb;
  if (!FPDF_SaveIncrementalObjects(doc, &w.fw, n ? list : NULL, n)) {
    free(w.data);
    return -1;
  }
  Buf b = {w.data, w.len};
  FPDF_FILEACCESS acc = {(unsigned long)w.len, GetBlock, &b};
  int ok = FPDF_VerifyIncrementalSave(doc, &acc, mismatch);
  free(w.data);
  return ok ? 1 : 0;
}

static int RunOp(FPDF_DOCUMENT doc, FPDF_PAGE page, const char* op) {
  FPDF_WCHAR name[8] = {'N', 'e', 'w', 0};
  FPDF_WCHAR value[8] = {'X', 0};
  if (!strcmp(op, "create")) return FPDFDoc_CreateOCG(doc, name) >= 0;
  if (!strcmp(op, "default_off")) return FPDFDoc_SetOCGDefaultVisibility(doc, 0, 0);
  if (!strcmp(op, "string")) return FPDFDoc_SetOCGStringValue(doc, 0, "Name", value);
  if (!strcmp(op, "number")) return FPDFDoc_SetOCGNumberValue(doc, 1, "Foo", 3.0f);
  if (!strcmp(op, "array")) {
    float v[2] = {1, 2};
    return FPDFDoc_SetOCGNumberArray(doc, 1, "Bar", v, 2);
  }
  if (!strcmp(op, "delete")) return FPDFDoc_DeleteOCG(doc, 1) >= 0;
  int ret = 0;
  int annot_index = strstr(op, "_direct") ? 1 : 0;
  FPDF_ANNOTATION a = FPDFPage_GetAnnot(page, annot_index);
  if (!a) return 0;
  if (!strncmp(op, "annot_setocg", 12)) {
    ret = FPDFAnnot_SetOCG(doc, a, 0);
  } else if (!strncmp(op, "membership", 10)) {
    int idx[2] = {0, 1};
    ret = FPDFAnnot_SetOCMembership(doc, a, idx, 2);
  } else if (!strcmp(op, "ap_oc")) {
    int idx[1] = {1};
    ret = FPDFAnnot_SetAPOptionalContent(doc, a, FPDF_ANNOT_APPEARANCEMODE_NORMAL,
                                         idx, 1);
  }
  FPDFPage_CloseAnnot(a);
  return ret;
}

static int Op(const char* in, const char* op) {
  size_t len = 0;
  unsigned char* orig = ReadAll(in, &len);
  FPDF_DOCUMENT doc = FPDF_LoadMemDocument64(orig, len, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  int ret = RunOp(doc, page, op);
  uint32_t mod[64];
  int n = FPDFDoc_GetLastModifiedObjects(doc, NULL, 0);
  int n2 = FPDFDoc_GetLastModifiedObjects(doc, mod, 64);
  uint32_t mismatch = 0;
  int full = SaveVerify(doc, mod, n2, &mismatch);
  printf("{\"ret\":%d,\"count\":%d,\"modified\":[", ret, n);
  for (int i = 0; i < n2; ++i) printf("%s%u", i ? "," : "", mod[i]);
  printf("],\"listed\":%d,\"mismatch\":%u,\"omit\":{", full, mismatch);
  for (int i = 0; i < n2; ++i) {
    uint32_t rest[64];
    int m = 0;
    for (int j = 0; j < n2; ++j)
      if (j != i) rest[m++] = mod[j];
    uint32_t mm = 0;
    int r = SaveVerify(doc, rest, m, &mm);
    printf("%s\"%u\":%d", i ? "," : "", mod[i], r);
  }
  printf("}}\n");
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  free(orig);
  return 0;
}

static int Ids(const char* in) {
  FPDF_DOCUMENT doc = FPDF_LoadDocument(in, "");
  if (!doc) return 1;
  printf("{\"catalog\":%u,\"ocproperties\":%u}\n",
         FPDFDoc_GetCatalogObjectNumber(doc),
         FPDFDoc_GetOCPropertiesObjectNumber(doc));
  FPDF_CloseDocument(doc);
  return 0;
}

static int Empty(const char* in, const char* mode, uint32_t listed) {
  size_t len = 0;
  unsigned char* orig = ReadAll(in, &len);
  FPDF_DOCUMENT doc = FPDF_LoadMemDocument64(orig, len, "");
  if (!doc) return 1;
  FPDF_PAGE page = FPDF_LoadPage(doc, 0);
  uint32_t list[1];
  int ok = 1;
  if (!strcmp(mode, "new")) {
    uint32_t annots = FPDFPage_GetAnnotsObjectNumber(page);
    list[0] = annots ? annots : FPDFPage_GetObjectNumber(page);
    FPDF_ANNOTATION a = FPDFPage_CreateAnnot(page, FPDF_ANNOT_TEXT);
    FS_RECTF r = {20, 40, 40, 20};
    FPDF_WCHAR empty[1] = {0};
    ok = a && FPDFAnnot_SetRect(a, &r) &&
         FPDFAnnot_SetAP(a, FPDF_ANNOT_APPEARANCEMODE_NORMAL, empty);
    if (a) FPDFPage_CloseAnnot(a);
  } else {
    list[0] = listed;
  }
  uint32_t mismatch = 0;
  int r = ok ? SaveVerify(doc, list, 1, &mismatch) : -2;
  printf("{\"result\":%d,\"mismatch\":%u}\n", r, mismatch);
  FPDF_ClosePage(page);
  FPDF_CloseDocument(doc);
  free(orig);
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 3) return 1;
  FPDF_InitLibrary();
  int rc = 1;
  if (!strcmp(argv[1], "ids")) rc = Ids(argv[2]);
  else if (!strcmp(argv[1], "op") && argc >= 4) rc = Op(argv[2], argv[3]);
  else if (!strcmp(argv[1], "empty") && argc >= 4)
    rc = Empty(argv[2], argv[3], argc >= 5 ? (uint32_t)atoi(argv[4]) : 0);
  FPDF_DestroyLibrary();
  return rc;
}
