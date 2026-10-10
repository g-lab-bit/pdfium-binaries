# Fork Runbook

This repository is a soft-fork of [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries).
It adds `patches/annot_api.patch` to expose additional annotation creation
and setter APIs in PDFium's public C interface.

The patch:

- Widens `FPDFAnnot_IsSupportedSubtype` to include three additional
  annotation subtypes that upstream rejects on the create path.
- Adds three new public functions: `FPDFAnnot_SetVertices`,
  `FPDFAnnot_SetLine`, `FPDFAnnot_SetLineEndings`.

All other build automation is inherited verbatim from bblanchon; this
runbook documents only the fork-specific maintenance flow.

---

## 1. Local toolchain (one-time)

PDFium uses Chromium's build system, so the host needs:

- A C++ toolchain for the target platform (MSVC on Windows, Xcode on
  macOS, system clang/gcc on Linux).
- `depot_tools` on `PATH`. Follow Google's tutorial at
  `https://commondatastorage.googleapis.com/chrome-infra-docs/flat/depot_tools/docs/html/depot_tools_tutorial.html`.
- Python 3 (depot_tools ships its own; system Python also works).
- Git.
- ~50 GB free disk for the PDFium source checkout and build trees.

On Windows: set `DEPOT_TOOLS_WIN_TOOLCHAIN=0` to use the locally
installed Visual Studio rather than Google's internal toolchain bundle.

---

## 2. Baseline build (toolchain verification)

```bash
git clone https://github.com/g-lab-bit/pdfium-binaries.git
cd pdfium-binaries

# Run for whichever <os> <cpu> you need. `gclient sync` (step 2) takes
# 30–60 min on the first invocation.
./build.sh <os> <cpu>
```

If the baseline build succeeds, the toolchain is correctly configured.
Subsequent runs from step 3 onward skip the checkout:

```bash
./build.sh -g 3 <os> <cpu>
```

---

## 3. Iterating on the patch

Edit `patches/annot_api.patch`, then re-run from step 3:

```bash
./build.sh -g 3 <os> <cpu>
```

A successful build produces a tarball in `build/<os>/<cpu>/`.

Spot-check the resulting DLL/dylib/so by listing its export table —
the three new symbols (`FPDFAnnot_SetVertices`, `FPDFAnnot_SetLine`,
`FPDFAnnot_SetLineEndings`) should be present alongside the
upstream `FPDFAnnot_*` symbols.

---

## 4. Merging upstream bblanchon updates

```bash
git remote add upstream https://github.com/bblanchon/pdfium-binaries.git
git fetch upstream
git merge upstream/master
git push origin master
```

Conflicts are typically limited to `steps/03-patch.sh` (where the
fork inserts an `apply_patch` line for `annot_api.patch`). Resolve
by keeping both upstream's changes and the inserted line.

---

## 5. Rebasing the patch after a PDFium version bump

When upgrading to a new PDFium branch:

1. Inspect the relevant section of upstream PDFium's
   `fpdfsdk/fpdf_annot.cpp` for line-number drift:
   - The `FPDFAnnot_IsSupportedSubtype` switch.
   - The block immediately after `FPDFAnnot_GetLine`.

2. If context lines in the patch no longer match, download the new
   source and adjust the hunk headers:

   ```bash
   curl -s "https://pdfium.googlesource.com/pdfium/+/refs/heads/chromium/NNNN/fpdfsdk/fpdf_annot.cpp?format=TEXT" \
     | base64 -d > /tmp/fpdf_annot.cpp
   cd /tmp && patch --verbose -p0 -i /path/to/patches/annot_api.patch fpdf_annot.cpp
   ```

3. Update `@@ -N,M +N,M @@` line counts to match the new context.
   Standard 3-line context.

4. Rebuild locally. Commit and push.

---

## 5.5 Fork initial setup (one-time)

After forking, the default `GITHUB_TOKEN` workflow permissions are
read-only on new forks. `build.yml` requests `actions: write`, which
fails with `startup_failure` until the default is relaxed:

```bash
gh api --method PUT repos/<owner>/pdfium-binaries/actions/permissions/workflow \
  --field default_workflow_permissions=write \
  --field can_approve_pull_request_reviews=false
```

Keep `can_approve_pull_request_reviews=false` regardless.

---

## 6. Triggering a CI release build

CI is reserved for cutting release artifacts that downstream
consumers will pin against.

```bash
gh workflow run build-one.yml \
  --repo <owner>/pdfium-binaries \
  --ref master \
  --field branch=chromium/<PDFIUM_VERSION> \
  --field version=<X.Y.Z.N> \
  --field target_os=<os> \
  --field target_cpu=<cpu>
```

Monitor at `https://github.com/<owner>/pdfium-binaries/actions`.

Build time is roughly 20–30 minutes on GitHub's free runners (longer
for V8 variants). The completed run produces a per-platform tarball
as an artifact.

---

## 7. Publishing the release

After CI goes green:

```bash
# Tag and push
git tag chromium/<PDFIUM_VERSION>-fork-pN
git push origin chromium/<PDFIUM_VERSION>-fork-pN

# Download the artifact from the workflow run
gh run download <RUN_ID> --repo <owner>/pdfium-binaries --dir /tmp/artifacts/

# Compute SHA256 for downstream pinning
sha256sum /tmp/artifacts/*/pdfium-*.tgz

# Publish the release
gh release create chromium/<PDFIUM_VERSION>-fork-pN \
  --repo <owner>/pdfium-binaries \
  --title "PDFium chromium/<PDFIUM_VERSION> — fork pN" \
  --notes "Annotation API patch applied to PDFium chromium/<PDFIUM_VERSION>." \
  /tmp/artifacts/*/pdfium-*.tgz
```

---

## 8. Downstream consumption

Downstream consumers fetch the published tarball via a
content-addressed URL (SHA256-pinned). Typical CMake idiom:

```cmake
FetchContent_Declare(pdfium_prebuilt
  URL "https://github.com/<owner>/pdfium-binaries/releases/download/chromium%2F<PDFIUM_VERSION>-fork-pN/pdfium-<os>-<cpu>.tgz"
  URL_HASH SHA256=<sha256-from-step-7>
)
```

The SHA256 pin gives downstream a tamper-evidence check independent
of this repository's access controls.

---

## 9. Local override during patch development

While iterating on the patch (before publishing a release),
downstream can point at the locally-staged build artifact instead of
a GitHub URL:

- **Local file URL** in `FetchContent_Declare`:
  ```cmake
  URL "file:///path/to/pdfium-binaries/build/<os>/<cpu>/pdfium-<os>-<cpu>.tgz"
  ```

- **Direct staging directory** (fastest inner loop):
  ```cmake
  set(PDFium_DIR "/path/to/pdfium-binaries/staging")
  find_package(PDFium REQUIRED)
  ```

Don't commit local overrides; gate them behind a developer-machine
file (`CMakeUserPresets.json`) or an environment variable.

---

## 10. Upgrade cadence

Expected: a handful of PDFium upgrades per year.
Typical cost: 2 hours (patch applies cleanly) to 1 day (context
fixup required).

Per upgrade:

- [ ] `git merge upstream/master`
- [ ] Verify the patch's context lines still match new PDFium source
- [ ] Rebuild locally
- [ ] Trigger CI for each shipped platform
- [ ] Publish a new release tag
- [ ] Notify downstream consumers of the new URL + SHA256

---

## 11. `incremental_objects.patch` (fork-p9): incremental save + verification

Applied by `steps/03-patch.sh` after `fork_p8.patch`. Touches
`core/fpdfapi/edit/cpdf_creator.{h,cpp}`, `core/fpdfapi/parser/cpdf_parser.{h,cpp}`,
`fpdfsdk/fpdf_save.cpp`, `public/fpdf_save.h`. Xref/trailer layout is ported from
pdf.js `src/core/writer.js` (Apache-2.0, credited in the code). The stock
`FPDF_SaveAsCopy(FPDF_INCREMENTAL)` path is unchanged.

### APIs (`public/fpdf_save.h`; full contract in the header comments)

- `FPDF_SaveIncrementalObjects(doc, file_write, const uint32_t* objnums, size_t count)`:
  writes the original file verbatim (including bytes before `%PDF-`), then ONE
  incremental update holding exactly the listed objects plus the objects created
  since load that are reachable from them through new objects. The xref section type
  follows the original's last section; a stream gets /Type/XRef, a self entry and
  a sized /W. The trailer has /Root /Info /Encrypt(same) /ID /Size /Prev.
  Nothing to write → exact copy, TRUE.
- `FPDF_VerifyIncrementalSave(doc, FPDF_FILEACCESS* saved, uint32_t* first_mismatch_objnum)`:
  TRUE proves:
  1. the original is an exact byte prefix of the saved file;
  2. the saved file parses without a rebuild and has the same /Root;
  3. every object held in memory, and every new object reachable from them,
     deep-equals the saved object (PDFium's post-load normalisations M1-M7 are
     tolerated exactly; see the comment in `fpdf_save.cpp`);
  4. the update sections, read directly with no decryption, define nothing else,
     free nothing, use no /XRefStm, and chain via /Prev exactly to the
     original's startxref.

  It does NOT prove intent (side effects on LISTED objects are saved), does not
  cover ungenerated page-object edits (call `FPDFPage_GenerateContent` first),
  and is not a structural validator. Run it on the bytes read back from disk.
  Windows: `m_FileLen` limits it to files under 4 GB.
- `FPDFPage_GetObjectNumber`, `FPDFPage_GetAnnotsObjectNumber`,
  `FPDFAnnot_GetObjectNumber`: `uint32_t` object numbers for building the list.

### Caller obligations

- List EVERY existing object modified since load, cumulatively: each call writes
  "original + one update" from the in-memory state, so repeated calls on one
  document do not stack.
- After adding or removing an annotation, list `/Annots` if it is indirect,
  otherwise the page dictionary.
- Never write to the source file while the document is open. On FALSE, discard
  the output (it may be partial) and fall back to a full save.

### Refusals (FALSE before any byte is written)

- No parser (new document).
- Rebuilt or damaged xref.
- Document opened through the linearized FPDFAvail path.
- A listed object that is missing, 0, or the /Encrypt dictionary.
- An unlisted loaded existing object that references a new object (e.g. a direct
  /Kids page made indirect, or form-fill widget /AP regeneration).
- A generation number ≠ 0 in a written object or in a reference it makes.
- An encrypted-metadata XML stream that would be written in plain text.
- A /Size or xref object number beyond `kMaxObjectNumber`.
- A classic-table offset ≥ 10^10 (sized by a counting pass).

After writing has started, any read or write failure, including the final
flush, also returns FALSE.

### Tests

`testing/incremental_objects/` runs against a CI artifact; pikepdf and qpdf are
mandatory and no case is skipped:

- `run_tests.py PDFIUM_DIR --qpdf PATH`: 95 cases with fixtures generated by
  `make_fixtures.py`. They cover save, refusals, writer failures, a session
  on one open document, the verify tolerances (with proof that each was
  exercised), corruptions and update-section attacks.
- `run_corpus.py PDFIUM_DIR --corpus DIR`: edit, repeat, touch, noop and
  render-all, with verify, over a PDF corpus; prints timings.

---

## 12. `annot_number_array.patch` (fork-p9): `FPDFAnnot_GetNumberArray`

Applied by `steps/03-patch.sh` after `incremental_objects.patch`. It touches
`fpdfsdk/fpdf_annot.cpp` and `public/fpdf_annot.h`.

`FPDFAnnot_GetNumberArray(annot, key, float* values, unsigned long count)` reads an
all-numeric array (direct or indirect, elements possibly indirect) from the
annotation dictionary, e.g. /C or /IC, whether or not /AP exists. Upstream
`FPDFAnnot_GetColor` refuses when /AP exists, so markup colours read back as black.

- It returns the element count n, or -1 if the key is absent, isn't an array, or has
  a non-numeric element.
- It copies min(n, count) values; a NULL `values` or `count == 0` queries the size.
- It modifies nothing and adds nothing to the document's object map. References
  are resolved through already-loaded objects or a parser copy that isn't stored.

Tests: `testing/annot_number_array/run_tests.py PDFIUM_DIR` (14 cases, including
non-mutation).

---

## 13. `ocg_view_state.patch` (fork-p9): render-only layer visibility

`FPDFDoc_SetOCGViewState(doc, ocg_index, state)` with state -1 (follow /D),
0 (hidden) or 1 (visible); `FPDFDoc_GetOCGViewState`;
`FPDFDoc_ClearOCGViewState`.

- The override is held on `CPDF_Document`, keyed by OCG object number. It is never
  written to /OCProperties or any dictionary, so saves, incremental saves and
  verify are unaffected (tested).
- It applies in `CPDF_OCContext::GetOCGVisible` for the "View" usage only. That
  covers page content, marked content in annotation appearances, and OCMDs. Every
  render entry point builds its context the same way
  (`CPDFSDK_RenderPageWithContext`, so FPDF_RenderPageBitmap[_Start/Continue] and the
  LOD paths; also FPDF_FFLDraw).
- Rendering with FPDF_PRINTING (usage "Print") still follows the document.
- A context caches each group's state at first use; a change shows from the next
  render.
- Not thread-safe; serialise with renders (Rapida's global PDFium lock).

## 14. `annot_ocmd.patch` (fork-p9): `FPDFAnnot_SetOCMembership`

`FPDFAnnot_SetOCMembership(doc, annot, ocg_indices, count)`:

- one distinct OCG → /OC is a reference to it;
- more → a new indirect `<< /Type /OCMD /OCGs [...] /P /AllOn >>` (never reused,
  so annotations never alias an OCMD);
- count 0 → /OC is removed.

Indices are validated before anything changes. `FPDFDoc_DeleteOCG` prunes a
deleted group from such an OCMD and removes /OC once none is left (tested).

## 15. `annot_ap_oc.patch` (fork-p9): `FPDFAnnot_SetAPOptionalContent`

`FPDFAnnot_SetAPOptionalContent(doc, annot, FPDF_ANNOT_APPEARANCEMODE_NORMAL,
ocg_indices, count)` puts nested `/OC /<name> BDC … EMC` marks, outermost =
`ocg_indices[0]`, on every object of the normal appearance.

- **Only for appearances built with `FPDFAnnot_AppendObject`.** The appearance is
  re-serialised from page objects.
- **Rebuild recipe for an annotation already in the file:**
  `FPDFAnnot_SetAP(annot, NORMAL, NULL)` on a fresh handle, then `AppendObject`.
- **Output goes to a NEW stream object** with its own copy of /Resources; /AP /N is
  repointed and the annotation's object list is bound to that copy, so later
  `AppendObject`/`UpdateObject` calls edit the new stream and its resources. The
  old stream and its /Resources are never modified. `FPDF_SaveIncrementalObjects` therefore writes it as a new object
  reachable from the listed annotation, and `FPDF_VerifyIncrementalSave` passes
  (tested: a new annotation, a rebuilt one, a reloaded one, and a second call).
- **/Properties names:** an existing entry is reused only if it already refers to
  the group; new names are unused `/RpOC<n>`; stale `/RpOC<n>` entries that
  refer to an OCG are removed (tested with a collision fixture).
- count 0 removes the marks. Objects appended later need a new call.
  `FPDFDoc_DeleteOCG` does not remove these marks.

Readers: PDFium and pdf.js hide the markup when any group is off. PDFKit ignores
optional content in annotation appearances entirely (marked content, AP /OC,
inner form /OC: tested).

Stock limits:
- `FPDF_RenderPageBitmap` never draws widget annotations.
- `FPDF_FFLDraw` draws widgets without an optional-content context, so /OC
  inside widget appearances is ignored there (for /D and the view state).

Tests: `testing/ocg_layers/run_tests.py PDFIUM_DIR` (31 cases).

---

## 16. `ocg_objnums.patch` (fork-p10): which existing objects a layer call modified

- `FPDFDoc_GetCatalogObjectNumber`, `FPDFDoc_GetOCPropertiesObjectNumber` (0 when
  /OCProperties is direct in the catalog: list the catalog).
- `FPDFDoc_GetLastModifiedObjects(doc, buf, count)`: the existing indirect objects
  modified by the most recent of these calls: CreateOCG,
  SetOCGDefaultVisibility, SetOCGString/Number/NumberArray, DeleteOCG,
  FPDFAnnot_SetOCG, SetOCMembership, SetAPOptionalContent.
  - Each call clears the list when it starts.
  - How it works: after validating its arguments, the call serialises every
    existing object it can touch, before and after, and reports the ones that
    changed.
  - That set is the catalog plus the optional content structures, collected
    by a structural walk that identifies them by their POSITION in the graph:
    /OCProperties, its /OCGs, /D and /Configs and their arrays (/ON, /OFF,
    /Locked, /Order, /RBGroups, /AS), OCG dictionaries, and OCMDs with /OCGs
    and /VE.
  - A dictionary at a structure position is entered whatever its /Type
    (fork-p11 fix: fork-p10 skipped a typed /OCProperties or configuration),
    except a page, page tree node, catalog or annotation. Anything else these
    link to (pages, catalog, resources, streams) is neither followed nor
    recorded, so the walk stays bounded.
  - DeleteOCG adds pages, /Annots, annotations and their /OC structures;
    annotation calls add the annotation and its holders.
  - So a change in a direct sub-object is reported through its indirect holder.
    The result is exact by construction, without per-path bookkeeping.
- Tests: `testing/ocg_objnums` (125 cases) covers every call on five layouts, each
  also with /Type on /OCProperties and on every configuration (fork-p10 fails 27
  of those). The fifth layout is
  a hostile fixture whose OC structures link the page, catalog, page tree and a
  5 MB stream. It also times DeleteOCG on 200 pages x 25 annotations. The
  first four layouts are:
  /OCProperties indirect, direct in the catalog, inside an ObjStm, and with
  /OCGs, /D and arrays as separate objects. The report is sufficient (listing it
  saves and verifies TRUE) and each entry necessary (omitting any one refuses or
  verifies FALSE).

## 17. `verify_empty_stream.patch` (fork-p10)

`FPDF_VerifyIncrementalSave` decodes Flate-normalised saved streams explicitly.
`CPDF_StreamAcc` returns the raw bytes when decoding yields nothing, so an empty
stream (e.g. a comment reply's 0-byte /AP) compared as non-empty. Tested in
`testing/ocg_objnums`.

## 18. `filter_unchanged.patch` (fork-p10): `FPDF_FilterUnchangedObjects`

`FPDF_FilterUnchangedObjects(doc, objnums, count)` removes, in place, every
listed existing object whose in-memory value equals the loaded file's.

- Same deep compare as verify. The original is parsed via
  `CPDF_Parser::ParseIndirectObject` and not stored in the document's map.
- Never-loaded objects are removed. New objects are kept.
- If the result is 0 and nothing new is reachable, the save is an exact copy, so
  the caller may skip writing.

Tests: `testing/filter_unchanged` (17 cases: plain, RC4 + ObjStm, AES-256 + ObjStm,
including a revert inside a direct /Annots array).

## 19. `annot_oc_membership.patch` (fork-p12): `FPDFAnnot_GetOCMembership`

`FPDFAnnot_GetOCMembership(doc, annot, buffer, buflen)` reads back what
`FPDFAnnot_SetOCMembership` writes, as indices into `/OCProperties /OCGs`
(`FPDFAnnot_GetOCGIndex` returns -1 for an OCMD):

- /OC an OCG → 1 index; an OCMD → its /OCGs (an OCG or an array, direct or
  indirect) in order (duplicates once) when it means "all of them": `/P /AllOn`,
  or a single group with `/AnyOn` (the default) or `/AllOn`; no /VE;
- returns the total count and copies up to `buflen` (`buflen` 0 sizes);
  0 = no /OC, `/OC null`, or an empty /OCGs; -1 = bad arguments, or a
  membership that is not an all-on set of listed groups (`/AllOff`/`/AnyOff`
  even with one group — those mean "visible when OFF"; `/AnyOn` over 2+
  groups; /VE; a direct OCG; a member missing from /OCGs).

Rapida uses it to re-apply a markup's layer chain to its rebuilt appearance
(`FPDFAnnot_SetAPOptionalContent`) and to show the markup's layer.

Tests: `testing/ocg_layers/run_tests.py` (membership read-back after every
`SetOCMembership` case and after `DeleteOCG`; ten /OC shapes other tools write;
buffer sizing and bad arguments; one-group /AllOff and /AnyOff, indirect
/OCGs array, direct OCMD, empty /OCGs, /OC null).

## 20. `annot_undo_wrap.patch` (fork-p13): exact undo + appearance wrapper

Exact undo (Rapida, founder decision 2026-10-08):
- `FPDFAnnot_SaveState(doc, annot)` → id: a deep copy of the annotation
  dictionary, held by the document (never written);
  `FPDFAnnot_RestoreState(doc, annot, id)` puts every entry back (same object),
  so an undone edit is no change for `FPDF_FilterUnchangedObjects`;
  `FPDFPage_InsertAnnotState(doc, page, id, index)` puts a removed annotation
  back at its /Annots position — the same indirect object (dictionary restored)
  or a direct copy; `FPDFAnnot_ReleaseState`.
- `FPDF_FilterUnchangedObjects` / `FPDF_VerifyIncrementalSave` (M6): an empty
  `/Annots` on a page equals no `/Annots` (add + remove an annotation).

Appearance wrapper (Rapida invariant 7, another tool's markup):
`FPDFAnnot_WrapAppearance(doc, annot, tint_rgb, ocg_indices, count)` — /AP /N
becomes a new form drawing the ORIGINAL form unchanged, plus an optional tint
(user colour, soft-masked by the original's alpha, `/BM /Color` in a separate
graphics state — pdf.js drops one when both share one), wrapped in nested
`/OC` marks for the layers. `/RpOriginal` records the original; rewrapping
starts from it; no tint and no layers restores the original /N. The original
form object is never modified.

Tests: `testing/annot_state/run_tests.py PDFIUM_DIR`.

## 21. `annot_wrap_color.patch` (fork-p14): the wrapper writes /C

PDFium's `FPDFAnnot_SetColor` refuses while `/AP /N` exists, so after
`FPDFAnnot_WrapAppearance` nothing could write the markup's `/C` (Rapida founder
decision 2026-10-08: the wrapper writes it).
- A tint writes `/C` = the tint; the tinted wrapper records `/RpTint` and the
  `/C` from before the first tint as `/RpOriginalC` (an array, or `/None`).
- No tint (unwrap, or rewrap for layers only) puts that first `/C` back, or
  removes `/C` when there was none. Retinting keeps the first `/C`.
- `FPDFAnnot_GetAppearanceTint(annot, rgb)`: the current tint, false when there
  is none (not wrapped, or layers only) — so a layer change can keep the tint.

Tests: `testing/annot_state/run_tests.py PDFIUM_DIR` (`color` cases).

## 22. `annot_oc_render.patch` (fork-p15): annotations follow their own /OC

Applied by `steps/03-patch.sh` after `annot_wrap_color.patch`. Stock PDFium
draws an annotation whatever its `/OC` says (`CPDF_AnnotList::DisplayPass`
checks only the hidden / print / no-view flags), so another tool's markup in
optional content — `/OC` on its dictionary, no marked content in its `/AP` —
never hid with its layer, by `/D` or by the view state (Rapida ra-wom0a).
Acrobat and pdf.js honour annotation `/OC` (ISO 32000-1 8.11.3.3).

- `CPDF_AnnotList::DisplayAnnots(..., const CPDF_RenderOptions* options =
  nullptr)`: with options, an annotation whose `/OC` (an OCG or an OCMD) is off
  under the options' optional-content context is skipped.
- `CPDFSDK_RenderPageWithContext` (FPDF_RenderPageBitmap[_Start/Continue] and
  the LOD paths) passes its options, so the usage is the render's: View (with
  `FPDFDoc_SetOCGViewState`, `ocg_view_state.patch`) or, with FPDF_PRINTING,
  Print, which follows the document.
- Other callers pass no options and are unchanged. FPDF_FFLDraw draws widgets
  through `CPDF_Annot::DrawAppearance` without a context, as before.
- Annotations whose `/AP` already carries the layer as marked content
  (`annot_ap_oc.patch`) hide the same way: one check, consistent results.

### Tests
`testing/ocg_layers/run_tests.py` ("annot /OC" cases): a red Square with a
plain `/AP` and only a dictionary `/OC` is drawn with its group on; hidden by
the view state and by `/D`; shown again when the view state follows `/D`;
printed by the document's state (not the view override); an OCMD `/AllOn`
hides with any group off, `/AnyOn` stays with one on; no `/OC`, always drawn.

