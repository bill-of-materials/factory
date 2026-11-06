# Multi-arch build, release & registry pipeline — design

Date: 2026-11-06
Repo: `bill-of-materials/factory` (public, GitHub org `bill-of-materials`)

## Intent

`factory` is a library of hardened OCI images (`alpine3`, `debian13-slim`,
`ninjam`, `snapcast`, more to come), each in its own top-level folder with a
`Dockerfile`, a `structure-test.yaml` and an optional `platforms.txt`.

The owner wants a CI that is fully automated end to end. Renovate opens PRs
when a base image or dependency changes, the owner approves, and everything
after the merge happens without them: multi-arch build, tests on every
architecture, publishing, signing, attestations, versioning, release notes,
and registry cleanup.

The owner also wants the GitHub **Packages** pages to be clear and useful.
Each published version should come with a short "readme" that has the pull
commands and a changelog, and that links the build pipeline run, the commit,
the signature and the attestations.

### Why now (current problems, verified in CI logs and workflow source)

1. **Multi-arch tests fail.** `container-structure-test` is run without
   `--platform`. It asks Docker for `linux/amd64` and fails on arm images:
   `image with reference local/debian13-slim:test was found but its platform (linux/arm64) does not match the specified platform (linux/amd64)`.
2. **Release rebuilds instead of publishing what was tested.**
   `release.yml` runs a fresh `build-push-action` after `validate`.
3. **Release change detection is broken.** `release.yml` is triggered by
   `workflow_run`, where `github.event.before` is empty. It falls back to
   diffing only the last commit, so changes can be missed.
4. **Only the multi-arch index is signed.** Per-arch manifests are not
   (`cosign sign` without `--recursive`).
5. **Registry is cluttered.** Every push adds a full-sha tag. Buildkit's
   in-index provenance/SBOM manifests show up as `unknown/unknown` platforms
   on the package page. Nothing is ever cleaned up.
6. **No versions, no changelog, no description** on packages.

## Goals

- Every architecture declared for an image is built **and** tested on every
  PR and on every release.
- The bytes published are the bytes that were tested on `main`. Publishing
  never does a second, untested build.
- Each image is versioned independently with automatic semver. Nobody ever
  types a version.
- Every published multi-arch image **and each per-arch image inside it** is
  signed with cosign (keyless, GitHub OIDC), verifiable with `cosign verify`.
  SBOM and SLSA provenance are GitHub attestations, verifiable with
  `gh attestation verify`. They are kept in GitHub's attestation store, not
  pushed to the registry, so each release adds only its 4 signature
  entries to the package (index plus each architecture).
- Each image version gets a GitHub Release that serves as its readme:
  pull commands, architectures, changelog, links, verify commands.
- The GHCR package page shows a short description, readable version tags,
  only real platforms (no `unknown/unknown`), and stays small through
  automatic cleanup.
- Renovate stays the driver. Its configuration changes little, and digests
  stay pinned.

## Non-goals

- Auto-merging Renovate PRs. The owner approves every PR.
- Automatically rebuilding dependent images (`ninjam`, `snapcast`) when
  `alpine3` is republished. That stays a Renovate PR the owner approves (see
  "Image dependency chain").
- A GHCR-rendered per-package README. GHCR can't do this, and the GitHub
  Release fills that role instead.
- Publishing to registries other than `ghcr.io`.
- Scheduled rebuilds without a source change.
- Enforcing commitlint in CI (it may be added later, but isn't required).

## Anti-goals (failure even if it "works")

- Publishing any image, or any architecture, that didn't pass its structure
  tests.
- Publishing any per-arch image without a signature.
- Cleanup deleting something still needed: a kept release, its per-arch
  children, or its signatures/attestations. (Digests pinned in Dockerfiles
  are deliberately *not* protected; see "Migration / first run".)
- Increasing the number of PRs or approvals the owner has to handle.
- Requiring a manual step (version numbers, tags, cleanup runs) in normal
  operation.

## Constraints

- Public repo, so free native ARM runners (`ubuntu-24.04-arm`) are available.
- Digest pinning everywhere stays: base images in `FROM`, actions in workflows.
- Commit messages follow the repo's conventional-commit types
  (`.commitlintrc.yaml`: `bump, chore, ci, clean, doc, feat, fix, hotfix, maint, refact, style, tests`).
  Renovate writes `chore(deps): …`. Scopes do **not** name images.
- `platforms.txt` keeps its current meaning: comma-separated platforms, with
  the default `linux/amd64,linux/arm64,linux/arm/v7`.
- The required status check `validation-required` keeps its name, because
  branch protection may depend on it.
- Deleting GHCR package versions from Actions requires each package to give
  this repository **Admin** access in "Manage Actions access". This is a
  one-time manual setup that must be documented in the README (see
  "Setup notes").

## Decided by the owner

- **Auto semver per image**, derived from commits.
- **One GitHub Release per image per new version**, with git tag
  `<image>/vX.Y.Z` (e.g. `ninjam/v1.4.2`). A busy Releases page is fine.
- **Automatic cleanup**, run automatically after every release (plus a
  weekly safety run). Released versions are deleted once 10 newer released
  versions of the same image exist, so **the 10 most recent releases per
  image are kept**. Untagged leftovers that don't belong to a kept release
  are deleted.
- Renovate approval flow stays as is, and digests stay pinned.

## Design

### Workflows overview

| Workflow | Trigger | Purpose |
|---|---|---|
| `validate.yml` | `pull_request` | Build + structure-test every changed image on every declared platform. Publishes nothing. |
| `release.yml` | `push` to `main` | For each image with unreleased changes: build per arch, push by digest, test that digest, assemble multi-arch index, tag, sign, attest, write GitHub Release, then run cleanup. |
| `cleanup.yml` | `workflow_call` (from release), `schedule` (weekly), `workflow_dispatch` (with `dry-run` input, default true for manual runs) | Prune GHCR versions per the retention rule. |

The release no longer uses `workflow_run`. It runs on `push` to `main` and
does its own tests on the exact digests it will publish. `validate` stays
focused on PRs. (Builder may also keep `validate` on `push` to `main` if
useful, but it must not gate or duplicate the release.)

### Platforms → runners

| Platform | Runner | Emulation |
|---|---|---|
| `linux/amd64` | `ubuntu-24.04` | none |
| `linux/arm64` | `ubuntu-24.04-arm` | none |
| `linux/arm/v7` | builder's choice (x64 or arm runner) | QEMU (the ARM runners can't execute 32-bit ARM natively) |
| anything else declared | `ubuntu-24.04` | QEMU |

The same mapping is used by `validate` and `release`.

### Fix for the structure tests

`container-structure-test` must be called with `--platform <matrix platform>`.
The binary that is downloaded must match the **runner** architecture
(`linux-amd64` or `linux-arm64`). Its version stays pinned. (My call: add a
Renovate custom regex manager so that version is bumped like everything
else. This is the only Renovate config change, and the builder may skip it
if it proves awkward.)

### Change detection & versioning (release)

Change detection is **based on tags, not on push diffs**. That makes it
self-healing: a failed or skipped run is picked up by the next one.

For each folder `<image>/` that contains a `Dockerfile`:

1. Find the latest git tag matching `<image>/v*` (semver sort).
2. No tag → this image's first release is **`1.0.0`**.
3. Otherwise, list non-merge commits in `<tag>..HEAD` that touch `<image>/`.
   My call: changes to `*.md` files alone don't trigger a release.
   None → nothing to release for this image.
4. Bump level = highest across those commits:
   - **major**: `type!:` / `type(scope)!:` in the subject, or a
     `BREAKING CHANGE:` footer
   - **minor**: type `feat`
   - **patch**: every other type, including Renovate's `chore(deps)`, and
     also unparseable subjects
5. The changelog for the release = those commits (subject, short sha linked
   to the commit, PR link if available), grouped by bump category.

Both squash-merge and merge-commit workflows must produce the same result,
which is why the commits counted are the non-merge commits that touch the
folder.

The git tag `<image>/vX.Y.Z` is created **only after** the image is
published and signed. This way a failed release leaves no tag and is retried
on the next push to `main`. (It can also be retried by re-running the
workflow. My call: also allow `workflow_dispatch` on `release.yml` to retry
without a new commit.)

### Release job, per image (order matters)

1. **Build per platform** on its runner, with buildx. Push **by digest
   only** (no tags) to `ghcr.io/bill-of-materials/<image>`. Buildkit's own
   `provenance` and `sbom` are **disabled**, because they are what produces
   the `unknown/unknown` entries. Build args/labels as today, plus
   `org.opencontainers.image.version=X.Y.Z`.
2. **Test each per-platform digest** with container-structure-test
   (`--platform`, pulled by digest) on a runner able to run it (same
   mapping as above). Any failure stops the image's release.
3. **Assemble the multi-arch index** from the tested digests
   (`docker buildx imagetools create`), by digest. The tags `X.Y.Z`, `X.Y`,
   `X` and `latest` are applied only after steps 4–6 succeed (see "Version
   immutability"). Set index-level annotations:
   - `org.opencontainers.image.description`: the package description (see
     below)
   - `org.opencontainers.image.source`, `.revision`, `.version`,
     `.created`, `.licenses` if known
4. **Sign** the index with `cosign sign --recursive` (keyless) so the index
   and every per-arch manifest are signed.
5. **SBOM** per platform (e.g. syft/`anchore/sbom-action`, SPDX JSON).
   Attach it as a GitHub attestation (`actions/attest-sbom`) on each
   per-arch digest, and upload the SBOM files as assets of the GitHub
   Release. **No cosign SBOM attestation** (owner's decision: it would add
   one more registry entry per architecture for each release).
6. **Provenance** as a GitHub attestation
   (`actions/attest-build-provenance`) on the index and on per-arch
   digests. Keep the attestation URL(s) the action outputs for the release
   notes.

   My call: GitHub attestations are **not** pushed to the registry
   (`push-to-registry: false`). `gh attestation verify` reads them from
   GitHub's attestation API, so they stay verifiable without adding more
   entries to the package page.
7. **Create the git tag and GitHub Release** `<image>/vX.Y.Z` (title
   `<image> X.Y.Z`; my call: don't mark per-image releases as the repo-wide
   "Latest"). The notes template is below.
8. After all images in the run are done, **call `cleanup.yml`**.

**Version immutability (my call).** A published `X.Y.Z` never moves to
different bytes. So:
- the index is first created **untagged** (by digest) and is signed and
  attested by digest, and only then are the tags `X.Y.Z`, `X.Y`, `X` and
  `latest` applied;
- on retry, if `X.Y.Z` already exists in the registry with
  `org.opencontainers.image.revision == HEAD`, nothing is rebuilt; only the
  missing steps (attestation, git tag, GitHub Release) are completed;
- if `X.Y.Z` exists for a *different* revision (should never happen), the
  release fails loudly rather than overwrite it.

Images in the same push are released independently (`fail-fast: false`).
Releases are serialized with a concurrency group, without cancellation.

### Package description

Each image declares a one-line description in its Dockerfile
(`LABEL org.opencontainers.image.description="…"`). The CI appends
"Signed multi-arch image. Release notes, changelog & verification:
https://github.com/bill-of-materials/factory/releases?q=<image>" and writes
the result as the index-level annotation, which GHCR displays. Keep it under
512 characters, plain text. Images currently lacking that label get one as
part of this work.

### Release notes template (the per-image "readme")

```markdown
## <image> X.Y.Z

**Pull**
docker pull ghcr.io/bill-of-materials/<image>:X.Y.Z
docker pull ghcr.io/bill-of-materials/<image>@sha256:<index digest>

**Architectures:** linux/amd64 · linux/arm64 · linux/arm/v7  (each with its per-arch digest)

**Changes since <previous version or "first release">**
- feat: … (abc1234, #12)
- chore(deps): update docker.io/library/alpine:3 digest to … (def5678, #13)

**Build & supply chain**
- Commit: <link>
- Pipeline run: https://github.com/bill-of-materials/factory/actions/runs/<run id>
- Provenance attestation: <attestation url>
- SBOM attestation: <attestation url>
- SBOM files (SPDX JSON, one per architecture): attached to this release
- Signed with cosign (keyless, GitHub OIDC), index and every architecture

**Verify**
cosign verify ghcr.io/bill-of-materials/<image>@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/bill-of-materials/factory/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://ghcr.io/bill-of-materials/<image>@sha256:<digest> -R bill-of-materials/factory
```

The exact wording and layout are left to the builder. The content above is
required. Every command in it must actually work, and that's checked once
end to end before the work is considered done.

### Cleanup

Runs after every release (as a called workflow), weekly (`schedule`), and
manually (`workflow_dispatch`, dry-run by default when manual). It runs per
image package in `ghcr.io/bill-of-materials/*` that corresponds to a folder
in the repo.

**Keep set** (everything else in the package is deleted):
- The **10 most recent released versions** by semver (tags `X.Y.Z`). This
  includes their index manifests, every per-arch child manifest, and all
  signatures/attestations/SBOMs attached to any of those digests (cosign
  `.sig`/`.att` tags and OCI referrers alike).
- Anything that is the target of a floating tag (`X`, `X.Y`, `latest`). By
  construction these are within the 10, but this is a safety check.
- Anything pushed in the last 24 h (my call). This protects an
  in-progress release whose tags aren't set yet.

**Deleted:** old releases beyond 10, the legacy full-sha-tagged versions
from the old pipeline, and any untagged manifest not in the keep set.

**After deleting a released version,** append a line to its GitHub Release
notes: "This image version was removed from the registry on YYYY-MM-DD
(retention: last 10 releases)." The git tag and GitHub Release are kept as
history (my call).

**Safety:** cleanup logs the keep set and the delete set. If it can't
resolve the children or attachments of a kept version, it deletes nothing
for that package and fails loudly. After a real run, the kept latest
version's `cosign verify` must still pass (spot check).

### Image dependency chain (unchanged behaviour, made explicit)

`ninjam` and `snapcast` build `FROM ghcr.io/bill-of-materials/alpine3:latest@sha256:…`.
Flow on an upstream alpine update:

1. Renovate opens a PR bumping `alpine3/Dockerfile`, and the owner approves.
2. Release publishes `alpine3` with a patch bump. `latest` now points to
   the new digest.
3. Renovate notices the new `alpine3:latest` digest and opens a PR for the
   dependents, and the owner approves.
4. Release publishes `ninjam` and `snapcast` with a patch bump.

Dependents keep following `alpine3:latest` (not a version tag), which keeps
Renovate's current behaviour.

### Migration / first run

- The first release creates `1.0.0` for every existing image (no tags exist
  yet).
- Legacy sha-tagged versions are removed by the first cleanup, **including
  the `alpine3@sha256:9eee99e…` still pinned by `ninjam` and `snapcast`**
  (owner's decision: it contains known vulnerabilities, and nothing is in
  production yet). Digests pinned in Dockerfiles get no special protection.
  Consequences, accepted:
  - `ninjam` and `snapcast` 1.0.0 are built on that old base (they are built
    before cleanup runs); Renovate's follow-up PR moves them to `alpine3`
    1.0.0 and produces their 1.0.1.
  - Until that PR is merged, any rebuild of `ninjam`/`snapcast` fails because
    their pinned base is gone. The Renovate PR itself fixes this.
- The README gains: an image table (name, description, platforms, link to
  package and releases), a "Verify an image" section, a short explanation
  of versioning (how bumps are chosen), retention (last 10 releases), and
  the setup notes below. The mermaid supply-chain diagram is updated to
  match.

### Setup notes (one-time, manual, documented in README)

- For each package in GHCR: Package settings → Manage Actions access → add
  `bill-of-materials/factory` with **Admin** role (needed to delete
  versions), and make sure the package is public.
- Branch protection: `validation-required` stays the required check.

## Left to the builder

- Script language and layout (my call: small bash scripts under
  `.github/scripts/` or a single composite action, with `jq`/`gh`. No
  semantic-release or release-please, because neither handles "version by
  folder, bump by commit type" cleanly).
- Exact action choices and versions (all pinned by digest, so Renovate
  tracks them).
- Whether `arm/v7` emulation runs on an x64 or arm64 runner.
- How build → test → merge is split into jobs, and how digests are passed
  between them (artifacts or outputs).
- Exact release-notes formatting.
- Whether `validate` also runs on `push` to `main`.

## Done means

- A PR touching each image passes `validate` on every declared platform
  (including `arm/v7`) on the real CI.
- Merging produces, for each changed image: tags `X.Y.Z`/`X.Y`/`X`/`latest`,
  a package page showing only real platforms and the description, a GitHub
  Release with working pull and verify commands, `cosign verify` passing on
  the index and on each per-arch digest, and `gh attestation verify`
  passing.
- Cleanup has run automatically after the release and left only the keep
  set, with nothing still needed deleted.
