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
