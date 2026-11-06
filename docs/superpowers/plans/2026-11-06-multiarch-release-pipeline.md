# Multi-arch release pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `validate`/`release` workflows with a pipeline that tests every architecture, publishes exactly the tested bytes under per-image auto-semver, signs and attests them, writes a GitHub Release per version, and prunes GHCR automatically.

**Architecture:** All decision logic (which images, which runners, next version, changelog, release notes, package description, cleanup keep-set) lives in a small stdlib-only Python package under `.github/scripts/factory_ci/`, unit-tested with `unittest`, and called through one CLI (`.github/scripts/ci.py`). The workflows only orchestrate: build/push/sign/attest/tag with standard actions and `cosign`/`oras`/`crane`, calling the CLI for decisions. (Builder's call allowed by the spec: Python instead of bash, because version/changelog/cleanup logic needs real tests.)

**Tech Stack:** GitHub Actions, docker buildx, container-structure-test, cosign v3 (keyless), oras, crane, `actions/attest-build-provenance`, `actions/attest-sbom`, `anchore/sbom-action`, Python 3.12 stdlib, `gh` CLI, Renovate.

**Spec:** `docs/superpowers/specs/2026-11-06-multiarch-release-pipeline-design.md`

## Global Constraints

- Registry: `ghcr.io/bill-of-materials/<image>`; repo `bill-of-materials/factory`.
- Image = top-level folder containing a `Dockerfile`.
- Platforms from `<image>/platforms.txt` (comma-separated, whitespace ignored); default `linux/amd64,linux/arm64,linux/arm/v7`.
- Runners: `linux/amd64` → `ubuntu-24.04`, no QEMU; `linux/arm64` → `ubuntu-24.04-arm`, no QEMU; `linux/arm/v7` and any other platform → `ubuntu-24.04` with QEMU.
- container-structure-test always called with `--platform <platform>`; binary arch matches the runner (`amd64` / `arm64`); version pinned in one env var tracked by Renovate.
- Git tag / release name: `<image>/vX.Y.Z`; release title `<image> X.Y.Z`; first release `1.0.0`; releases created with `--latest=false`.
- Bump: major if subject matches `^\w+(\([^)]*\))?!:` or body contains `BREAKING CHANGE:`; minor if type is `feat`; patch otherwise (including unparseable subjects).
- Commits counted: non-merge commits touching `<image>/`, excluding paths matching `<image>/**/*.md`.
- Registry tags: `X.Y.Z`, `X.Y`, `X`, `latest` — applied only after sign + attest succeed. No sha tags. Buildkit `provenance: false`, `sbom: false`.
- `cosign sign --yes --recursive` on the index digest. No cosign SBOM attestation. GitHub attestations (provenance on index + per-arch, SBOM per-arch) with `push-to-registry: false`.
- Published `X.Y.Z` never moves: exists with `org.opencontainers.image.revision == HEAD` → resume (no rebuild); exists with another revision → fail.
- Package description = Dockerfile `LABEL org.opencontainers.image.description` + ` Signed multi-arch image. Release notes, changelog & verification: https://github.com/bill-of-materials/factory/releases?q=<image>`; ≤ 512 chars; set as index annotation.
- Cleanup keep set: 10 most recent semver releases + closure (children, `sha256-<hex>*` attachment tags and their children) + targets of `X`, `X.Y`, `latest` + anything created < 24 h ago. Runs after every release, weekly, and manually (dry-run default for manual).
- Removed-release note, appended once: `This image version was removed from the registry on YYYY-MM-DD (retention: last 10 releases).`
- Required check name stays `validation-required`.
- All actions pinned by full commit sha with `# vX` comment (Renovate keeps them updated). Look up current shas with `gh api repos/<owner>/<action>/commits/<tag> --jq .sha`; reuse shas already in the repo for actions already used.

## Review Focus

1. A single Renovate commit touching two image folders → both images get a patch release, and that commit appears in both changelogs. (Task 2 test `test_commit_touching_two_images_bumps_both`.)
2. Non-squash merge history (merge commit + its branch commits) → each branch commit counted once, the merge commit ignored. (Task 2 test `test_merge_commits_ignored`.)
3. Legacy sha-tagged versions from the old pipeline (no semver tag, older than 24 h) → deleted together with their children and attachments, even if a Dockerfile still pins them (owner's decision). (Task 6 test `test_legacy_sha_tagged_deleted`.)
4. A platform with no explicit mapping (e.g. `linux/riscv64`) → `ubuntu-24.04` with QEMU, not a crash. (Task 1 test `test_unknown_platform_falls_back_to_qemu`.)
5. Retry after a partial release → `X.Y.Z` present with revision HEAD resumes without rebuilding; present with another revision fails loudly. (Task 4 tests `test_resume_when_same_revision`, `test_fails_when_other_revision`.)

---

## File Structure

```
.github/scripts/ci.py                       CLI entry point (argparse subcommands)
.github/scripts/factory_ci/__init__.py
.github/scripts/factory_ci/images.py        image discovery, platforms, runner mapping, matrices, changed images
.github/scripts/factory_ci/versioning.py    commits since last tag, bump level, next version
.github/scripts/factory_ci/description.py   package description from Dockerfile label
.github/scripts/factory_ci/registry.py      crane/gh wrappers: manifest, revision lookup, versions list/delete, attestations
.github/scripts/factory_ci/notes.py         release notes rendering
.github/scripts/factory_ci/cleanup.py       pure keep/delete selection
.github/scripts/tests/__init__.py
.github/scripts/tests/helpers.py            temp git repo builder
.github/scripts/tests/test_images.py
.github/scripts/tests/test_versioning.py
.github/scripts/tests/test_description.py
.github/scripts/tests/test_release_plan.py
.github/scripts/tests/test_notes.py
.github/scripts/tests/test_cleanup.py
.github/workflows/validate.yml              rewritten
.github/workflows/release.yml               rewritten
.github/workflows/cleanup.yml               new
renovate.json                               + customManagers preset
README.md, */Dockerfile                     description labels, docs
```

Test command used everywhere below (run from repo root):

```bash
(cd .github/scripts && python3 -m unittest discover -s tests -t . -v)
```

Workflow lint used everywhere below:

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7 -color
```

---

### Task 1: Image discovery, runner mapping, PR matrix

**Files:**
- Create: `.github/scripts/ci.py`, `.github/scripts/factory_ci/__init__.py`, `.github/scripts/factory_ci/images.py`
- Create: `.github/scripts/tests/__init__.py`, `.github/scripts/tests/helpers.py`, `.github/scripts/tests/test_images.py`

**Interfaces:**
- Produces (`factory_ci.images`):
  - `DEFAULT_PLATFORMS: list[str] = ["linux/amd64", "linux/arm64", "linux/arm/v7"]`
  - `list_images(root: Path) -> list[str]` — sorted top-level dirs with a `Dockerfile`
  - `read_platforms(root: Path, image: str) -> list[str]`
  - `runner_for(platform: str) -> dict` → `{"runner": str, "qemu": bool, "cst_arch": "amd64"|"arm64"}`
  - `platform_slug(platform: str) -> str` — `linux/arm/v7` → `linux-arm-v7`
  - `build_matrix(root: Path, images: list[str]) -> list[dict]` — one entry per image×platform: `{"image", "platform", "slug", "runner", "qemu", "cst_arch"}`
  - `is_release_relevant(path: str) -> bool` — False for `*.md`
  - `changed_images(root: Path, base: str, head: str) -> list[str]` — `git diff --name-only base...head`, first path component, relevant paths only, must be in `list_images`
- Produces (`tests.helpers`): `GitRepo(tmpdir)` with `.write(path, text)`, `.commit(message, *paths) -> sha`, `.tag(name)`, `.merge(branch, message) -> sha`, `.checkout(branch, create=False)`, `.root: Path`; sets `user.name/email` locally.
- Produces (`ci.py`): `python3 .github/scripts/ci.py pr-matrix --base B --head H` printing `matrix=<json>` and `has_images=true|false` (sentinel `[{"image":"__none__","platform":"__none__","slug":"none","runner":"ubuntu-24.04","qemu":false,"cst_arch":"amd64"}]` when empty — keeps the old “no images” pattern).

- [ ] **Step 1: Write failing tests** in `test_images.py`:

```python
def test_default_platforms_when_no_file(self):
    self.assertEqual(read_platforms(root, "alpine3"), ["linux/amd64", "linux/arm64", "linux/arm/v7"])
def test_platforms_file_whitespace_ignored(self):  # " linux/amd64 ,\nlinux/arm64\n"
    self.assertEqual(read_platforms(root, "ninjam"), ["linux/amd64", "linux/arm64"])
def test_runner_mapping(self):
    self.assertEqual(runner_for("linux/amd64"), {"runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64"})
    self.assertEqual(runner_for("linux/arm64"), {"runner": "ubuntu-24.04-arm", "qemu": False, "cst_arch": "arm64"})
    self.assertEqual(runner_for("linux/arm/v7"), {"runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64"})
def test_unknown_platform_falls_back_to_qemu(self):
    self.assertEqual(runner_for("linux/riscv64"), {"runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64"})
def test_build_matrix_one_entry_per_platform(self):  # alpine3 default → 3 entries, slug "linux-arm-v7" present
def test_list_images_ignores_dirs_without_dockerfile(self):  # ".github", "docs" excluded
def test_changed_images_ignores_markdown_only(self):  # commit touching ninjam/README.md only → []
def test_changed_images_detects_dockerfile_change(self):  # commit touching snapcast/Dockerfile → ["snapcast"]
```

- [ ] **Step 2: Run tests, expect FAIL** (`ModuleNotFoundError: factory_ci`).
- [ ] **Step 3: Implement `images.py`, `helpers.py`, and `ci.py pr-matrix`.** `ci.py` uses `argparse` subparsers; output lines are written to stdout (workflows redirect to `$GITHUB_OUTPUT`).
- [ ] **Step 4: Run tests, expect PASS.** Also run `python3 .github/scripts/ci.py pr-matrix --base HEAD~5 --head HEAD` and check it prints valid JSON.
- [ ] **Step 5: Commit** — `feat(ci): add image discovery and platform runner mapping`

---

### Task 2: Versioning (bump level, next version, commits)

**Files:**
- Create: `.github/scripts/factory_ci/versioning.py`, `.github/scripts/tests/test_versioning.py`

**Interfaces:**
- Consumes: `tests.helpers.GitRepo`, `images.list_images`.
- Produces:
  - `@dataclass(frozen=True) Commit(sha: str, subject: str, body: str)`
  - `bump_level(c: Commit) -> Literal["major","minor","patch"]`
  - `bump(version: str, level: str) -> str`
  - `latest_tag(root: Path, image: str) -> str | None` — highest semver among `git tag --list '<image>/v*'`
  - `commits_since(root: Path, image: str, tag: str | None, head: str = "HEAD") -> list[Commit]` — `git log --no-merges --format=... <tag>..<head> -- <image>/ ':(exclude,glob)<image>/**/*.md'`, newest first; all history when `tag` is None
  - `@dataclass(frozen=True) VersionPlan(image: str, version: str, previous_version: str | None, bump: str, commits: list[Commit])`
  - `next_version(root: Path, image: str, head: str = "HEAD") -> VersionPlan | None` — None when no relevant commits since tag; first release → `version="1.0.0"`, `previous_version=None`, `bump="initial"`

- [ ] **Step 1: Write failing tests:**

```python
def test_bump_levels(self):
    self.assertEqual(bump_level(Commit("a", "feat(ninjam): add x", "")), "minor")
    self.assertEqual(bump_level(Commit("a", "chore(deps): update alpine digest", "")), "patch")
    self.assertEqual(bump_level(Commit("a", "fix!: drop root user", "")), "major")
    self.assertEqual(bump_level(Commit("a", "feat(base)!: rename user", "")), "major")
    self.assertEqual(bump_level(Commit("a", "fix: x", "body\n\nBREAKING CHANGE: y")), "major")
    self.assertEqual(bump_level(Commit("a", "Update stuff", "")), "patch")
def test_bump(self):
    self.assertEqual(bump("1.2.3", "patch"), "1.2.4"); self.assertEqual(bump("1.2.3", "minor"), "1.3.0"); self.assertEqual(bump("1.2.3", "major"), "2.0.0")
def test_first_release_is_1_0_0(self):  # no tag, one commit in alpine3/ → version "1.0.0", previous None
def test_no_release_without_changes(self):  # tag alpine3/v1.0.0 at HEAD → None
def test_markdown_only_change_no_release(self):  # tag, then commit ninjam/README.md → None
def test_highest_bump_wins(self):  # tag v1.2.3, commits chore + feat → "1.3.0", 2 commits
def test_semver_sort_not_lexical(self):  # tags alpine3/v1.9.0 and alpine3/v1.10.0 → latest_tag == "alpine3/v1.10.0"
def test_commit_touching_two_images_bumps_both(self):  # one chore(deps) commit editing ninjam/Dockerfile + snapcast/Dockerfile → both plans patch, commit sha in both
def test_merge_commits_ignored(self):  # branch with 2 commits merged with --no-ff → 2 commits, merge sha absent
def test_other_image_tags_ignored(self):  # tag ninjam/v5.0.0 does not affect alpine3
```

- [ ] **Step 2: Run tests, expect FAIL** (import error).
- [ ] **Step 3: Implement `versioning.py`.** Use `git log -z --format=%H%x1f%s%x1f%b` for safe parsing; semver sort by tuple of ints; ignore tags not matching `^<image>/v\d+\.\d+\.\d+$`.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ci): add per-image semver computation`

---

### Task 3: Package descriptions (labels + generator)

**Files:**
- Create: `.github/scripts/factory_ci/description.py`, `.github/scripts/tests/test_description.py`
- Modify: `alpine3/Dockerfile`, `debian13-slim/Dockerfile`, `ninjam/Dockerfile`, `snapcast/Dockerfile` (add `LABEL org.opencontainers.image.description="…"` next to the title label)
- Modify: `ci.py` (subcommand `description --image X`)

**Interfaces:**
- Produces: `SUFFIX_TEMPLATE = " Signed multi-arch image. Release notes, changelog & verification: https://github.com/bill-of-materials/factory/releases?q={image}"`; `package_description(root: Path, image: str) -> str`; raises `ValueError(f"{image}: missing org.opencontainers.image.description label")` when absent. If too long, the label text is truncated with `…` so the total is ≤ 512; the suffix is never truncated.

Label texts (my copy, exact):
- alpine3: `Hardened Alpine 3 base image with a non-root app user, dumb-init, curl and CA certificates.`
- debian13-slim: `Hardened Debian 13 (trixie) slim base image with a non-root app user, dumb-init, curl and CA certificates.`
- ninjam: `NINJAM server (ninjamsrv) built from source on the alpine3 base image.`
- snapcast: `Snapcast server (snapserver) on the alpine3 base image.`

- [ ] **Step 1: Write failing tests:** `test_description_appends_suffix` (exact full string for a fixture Dockerfile), `test_missing_label_raises`, `test_long_label_truncated_to_512` (`len(...) == 512` and endswith suffix), `test_all_repo_images_have_description` (iterates real `list_images(REPO_ROOT)`, no exception).
- [ ] **Step 2: Run tests, expect FAIL.**
- [ ] **Step 3: Implement `description.py` + CLI; add the four labels.** Parse both `LABEL k="v"` and multi-line `LABEL a=… \` forms (the repo uses both).
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** — `feat(images): add package descriptions`

---

### Task 4: Release plan (resume / immutability)

**Files:**
- Create: `.github/scripts/factory_ci/registry.py`, `.github/scripts/tests/test_release_plan.py`
- Modify: `ci.py` (subcommand `release-plan`)

**Interfaces:**
- Consumes: `versioning.next_version`, `images.list_images`, `images.build_matrix`.
- Produces (`registry.py`):
  - `REGISTRY = "ghcr.io/bill-of-materials"`
  - `index_revision(ref: str) -> str | None` — `crane manifest <ref>` → `annotations["org.opencontainers.image.revision"]`; None when crane reports MANIFEST_UNKNOWN / not found (any other error raises)
  - `digest_of(ref: str) -> str` — `crane digest`
- Produces (`ci.py`): `release_plan(root: Path, head_sha: str, revision_lookup: Callable[[str], str | None]) -> list[dict]` with entries `{"image", "version", "previous_version", "bump", "mode": "build"|"resume"}`; raises `RuntimeError(f"{image}:{version} already published from {rev}")` on mismatch. CLI `release-plan --head-sha SHA` prints `plans=<json>`, `matrix=<build_matrix of mode=build images, each entry extended with "version", or sentinel>`, `has_builds=true|false`, `has_releases=true|false`.

- [ ] **Step 1: Write failing tests** (inject a fake `revision_lookup`, no network):
  - `test_build_when_version_absent` → mode `build`
  - `test_resume_when_same_revision` → mode `resume`, image not in build matrix
  - `test_fails_when_other_revision` → `RuntimeError` with message containing `already published`
  - `test_images_without_changes_excluded`
- [ ] **Step 2: Run tests, expect FAIL.**
- [ ] **Step 3: Implement.** The CLI looks up `f"{REGISTRY}/{image}:{version}"` via `index_revision`.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ci): add release plan with resume detection`

---

### Task 5: Release notes + attestation lookup

**Files:**
- Create: `.github/scripts/factory_ci/notes.py`, `.github/scripts/tests/test_notes.py`
- Modify: `.github/scripts/factory_ci/registry.py`, `ci.py` (subcommand `release-notes`)

**Interfaces:**
- Consumes: `versioning.Commit`, `versioning.commits_since`, `registry.digest_of`.
- Produces (`registry.py`):
  - `@dataclass Attestation(id: int, predicate_type: str, predicate: dict)`; `url` property → `https://github.com/bill-of-materials/factory/attestations/{id}`
  - `fetch_attestations(repo: str, digest: str) -> list[Attestation]` — `gh api repos/{repo}/attestations/{digest}`, decode `bundle.dsseEnvelope.payload` (base64 in-toto statement)
  - `platform_digests(index_ref: str) -> list[tuple[str, str]]` — `(platform, digest)` from the index manifest, skipping entries without a platform or with `unknown`
- Produces (`notes.py`):
  - `PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"`, SBOM type = any predicate type starting with `https://spdx.dev/Document`
  - `@dataclass ReleaseContext(image, version, previous_version, index_digest, platforms: list[tuple[str, str]], commits: list[Commit], repo, server_url, sha, run_id, provenance_url: str | None, sbom_urls: dict[str, str])`
  - `render_release_notes(ctx: ReleaseContext) -> str` — content of the spec's template; PR links parsed from `(#N)` in subjects; `previous_version None` → heading `Changes (first release)`
- CLI `release-notes --image X --version V --sha SHA --run-id ID --out notes.md --sbom-dir DIR`: resolves index digest (`crane digest :V`), platforms, commits since previous tag, attestations; writes notes and `DIR/<image>-<V>-<slug>.spdx.json` per platform from the SBOM attestation predicates. Works identically in `build` and `resume` mode (everything is read back from the registry and GitHub).

- [ ] **Step 1: Write failing tests** for `render_release_notes` with a fixed context:
  - contains `docker pull ghcr.io/bill-of-materials/ninjam:1.4.2` and `docker pull ghcr.io/bill-of-materials/ninjam@sha256:`+index digest
  - contains each platform with its digest
  - contains `https://github.com/bill-of-materials/factory/actions/runs/123`
  - contains commit link `https://github.com/bill-of-materials/factory/commit/<sha>` and `[#12](https://github.com/bill-of-materials/factory/pull/12)`
  - contains the exact `cosign verify` command with `--certificate-identity-regexp '^https://github.com/bill-of-materials/factory/\.github/workflows/release\.yml@refs/heads/main$'` and `--certificate-oidc-issuer https://token.actions.githubusercontent.com`
  - contains `gh attestation verify oci://ghcr.io/bill-of-materials/ninjam@sha256:… -R bill-of-materials/factory`
  - `test_first_release_heading` and `test_missing_attestation_url_omits_line` (no `None` text in output)
  - `test_decode_attestation_payload` for `fetch_attestations` parsing, using a canned JSON fixture and a patched `subprocess.run`
- [ ] **Step 2: Run tests, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ci): render per-image release notes`

---

### Task 6: Cleanup selection (pure logic)

**Files:**
- Create: `.github/scripts/factory_ci/cleanup.py`, `.github/scripts/tests/test_cleanup.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Version(id: int, digest: str, tags: tuple[str, ...], created_at: datetime)`
  - `class IncompleteGraph(Exception)`
  - `@dataclass Selection(keep: set[str], delete: list[Version], removed_releases: list[str])` — `removed_releases` = `X.Y.Z` tags on deleted versions
  - `select(versions: list[Version], children: dict[str, list[str]], now: datetime, keep_releases: int = 10) -> Selection`

Algorithm (not determined by the signature):
1. Roots = digests of the `keep_releases` highest semver `X.Y.Z` tags ∪ targets of tags matching `^\d+$`, `^\d+\.\d+$`, `latest` ∪ versions with `now - created_at < 24h`.
2. Closure to a fixed point: add `children[d]` for each kept `d`; add every version having a tag `sha256-<hex>` or `sha256-<hex>.<suffix>` where `sha256:<hex>` is kept.
3. If a kept digest is not a key in `children` → `IncompleteGraph(digest)` (the I/O layer must fetch every version's manifest; leaf manifests map to `[]`).
4. `delete` = versions whose digest is not kept, sorted by `created_at`.

- [ ] **Step 1: Write failing tests:**
  - `test_keeps_ten_most_recent_releases` (12 releases 1.0.0…1.11.0 → 1.0.0 and 1.1.0 deleted, `removed_releases == ["1.0.0","1.1.0"]`)
  - `test_semver_order_not_creation_order`
  - `test_children_and_signatures_of_kept_kept` (index → 3 children; `sha256-<idx>.sig`, `sha256-<child>.sig`, referrers tag `sha256-<idx>` whose index lists a referrer manifest → all kept)
  - `test_attachments_of_deleted_deleted`
  - `test_legacy_sha_tagged_deleted` (version tagged only with a 40-char sha, 2 days old, with children and a `.sig` → all deleted)
  - `test_untagged_orphans_deleted`, `test_recent_untagged_kept_24h`
  - `test_missing_children_raises` → `IncompleteGraph`
- [ ] **Step 2: Run tests, expect FAIL.**
- [ ] **Step 3: Implement `cleanup.py`.**
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ci): add registry retention selection`

---

### Task 7: Cleanup I/O + `cleanup.yml`

**Files:**
- Modify: `.github/scripts/factory_ci/registry.py`, `ci.py` (subcommand `cleanup`)
- Create: `.github/workflows/cleanup.yml`

**Interfaces:**
- Consumes: `cleanup.select`, `cleanup.Version`, `cleanup.IncompleteGraph`.
- Produces (`registry.py`):
  - `list_versions(org: str, image: str) -> list[Version]` — `gh api --paginate /orgs/{org}/packages/container/{image}/versions`; tags from `metadata.container.tags`; returns `[]` if the package is 404
  - `manifest_children(ref: str) -> list[str]` — `crane manifest`; index → `manifests[].digest`; image manifest → `[]`
  - `delete_version(org: str, image: str, version_id: int) -> None` — `gh api -X DELETE …/versions/{id}`
  - `mark_release_removed(repo: str, image: str, version: str, today: date) -> None` — `gh release view <image>/v<version> --json body`; append the exact removal line once (skip if already present or release missing)
- CLI `cleanup --org bill-of-materials [--image X] [--dry-run]`: all images when `--image` omitted; per image logs keep count, then every delete (`id digest tags`); continues to the next image on error; exit 1 if any image failed.
- `cleanup.yml`:
  - `on: workflow_call` (input `dry-run`, boolean, default `false`), `schedule: cron: "17 4 * * 1"`, `workflow_dispatch` (input `dry-run`, boolean, default `true`)
  - `permissions: contents: write, packages: write`; `concurrency: group: ghcr-cleanup, cancel-in-progress: false`
  - steps: checkout, `imjasonh/setup-crane` (pinned), `docker/login-action` to ghcr.io, then `python3 .github/scripts/ci.py cleanup --org bill-of-materials ${{ inputs.dry-run && '--dry-run' || '' }}` with `GH_TOKEN: ${{ github.token }}` (schedule → inputs null → real run)

- [ ] **Step 1: Write failing test** `test_cleanup_cli_dry_run_deletes_nothing` patching `registry.list_versions`, `registry.manifest_children`, `registry.delete_version`, `registry.mark_release_removed`: dry-run → `delete_version` never called; real → called with the selected ids and `mark_release_removed` called for each removed release.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement registry functions, CLI, `cleanup.yml`.**
- [ ] **Step 4: Run tests (PASS) and actionlint (no errors).** Then run read-only against the real registry: `python3 .github/scripts/ci.py cleanup --org bill-of-materials --dry-run` (needs a token with `read:packages`; if missing, ask the owner to run `gh auth refresh -s read:packages`). Expected: legacy sha-tagged versions listed for deletion, including `alpine3@sha256:9eee99e…`.
- [ ] **Step 5: Commit** — `feat(ci): add automatic GHCR cleanup workflow`

---

### Task 8: Rewrite `validate.yml`

**Files:**
- Modify: `.github/workflows/validate.yml` (full rewrite)

**Interfaces:**
- Consumes: `ci.py pr-matrix`, `ci.py` test suite.

Jobs:
- `ci-tests` (ubuntu-24.04): checkout, run the unit test command, run actionlint (docker image above).
- `discover`: `on: pull_request` only; checkout `fetch-depth: 0`; `python3 .github/scripts/ci.py pr-matrix --base ${{ github.event.pull_request.base.sha }} --head ${{ github.event.pull_request.head.sha }} >> "$GITHUB_OUTPUT"`.
- `structure-test`: matrix `include: fromJson(needs.discover.outputs.matrix)`, `fail-fast: false`, `runs-on: ${{ matrix.runner }}`, name `structure-test (${{ matrix.image }}, ${{ matrix.platform }})`; QEMU step `if: matrix.qemu`; buildx; `docker buildx build --platform <p> --load -t local/<image>:test <image>`; download `container-structure-test-linux-${{ matrix.cst_arch }}` at `${CST_VERSION}`; `container-structure-test test --platform <p> --image local/<image>:test --config <image>/structure-test.yaml`. Sentinel entry → echo and skip like today.
- `validation-required`: `needs: [ci-tests, discover, structure-test]`, `if: always()`, fails unless all three are `success`.
- Workflow-level `env: CST_VERSION: v1.22.1` preceded by `# renovate: datasource=github-releases depName=GoogleContainerTools/container-structure-test`.
- Triggers: `pull_request` only (push to `main` is handled by `release.yml`).

- [ ] **Step 1: Rewrite the workflow.**
- [ ] **Step 2: Run actionlint, expect no errors.**
- [ ] **Step 3: Local multi-arch smoke test** (reproduces the original bug and its fix on this machine, needs binfmt): `docker run --privileged --rm tonistiigi/binfmt --install arm64,arm`, then for `alpine3` and `linux/arm64`: build with `--load` as in the workflow, run `container-structure-test test --platform linux/arm64 …` (download the binary into the scratchpad). Expected: `PASS`; the same command without `--platform` reproduces `does not match the specified platform (linux/amd64)`.
- [ ] **Step 4: Commit** — `fix(ci): run structure tests on the target platform`

---

### Task 9: Rewrite `release.yml`

**Files:**
- Modify: `.github/workflows/release.yml` (full rewrite)

**Interfaces:**
- Consumes: `ci.py release-plan`, `ci.py description`, `ci.py release-notes`, `cleanup.yml` (`workflow_call`), `images.build_matrix` entry fields.

Triggers / globals: `on: push: branches: [main]` and `workflow_dispatch`; `concurrency: group: release-main, cancel-in-progress: false`; top-level `permissions: contents: read`; `env: CST_VERSION` (same Renovate comment), `REGISTRY: ghcr.io/bill-of-materials`.

Jobs:
1. **`plan`** (ubuntu-24.04, `packages: read`): checkout `fetch-depth: 0` (tags needed), setup-crane, login; `ci.py release-plan --head-sha ${{ github.sha }} >> $GITHUB_OUTPUT`. Outputs `plans`, `matrix`, `has_builds`, `has_releases`.
2. **`build`** (`if: has_builds == 'true'`; matrix from `plan.outputs.matrix`; `fail-fast: false`; `runs-on: matrix.runner`; permissions `packages: write, id-token: write, attestations: write, contents: read`):
   - QEMU if `matrix.qemu`; buildx; login.
   - `docker/build-push-action` (reuse sha in repo): `context: <image>`, `platforms: <platform>`, `provenance: false`, `sbom: false`, `outputs: type=image,name=${REGISTRY}/<image>,push-by-digest=true,name-canonical=true,push=true`, labels `org.opencontainers.image.{source,revision,created,version}` and build-args `IMAGE_SOURCE/IMAGE_REVISION/IMAGE_CREATED`. `created` = commit time `git log -1 --format=%cI` (my call: identical across platforms and on retry).
   - Test the pushed digest: `docker pull --platform <p> ${REGISTRY}/<image>@<digest>` then container-structure-test with `--platform`, binary `linux-${{ matrix.cst_arch }}`.
   - SBOM: `anchore/sbom-action` with `image: ${REGISTRY}/<image>@<digest>`, `format: spdx-json`, `output-file: sbom.spdx.json`, `upload-artifact: false`.
   - `actions/attest-sbom` and `actions/attest-build-provenance`: `subject-name: ${REGISTRY}/<image>`, `subject-digest: <digest>`, `push-to-registry: false`.
   - Upload artifact `digest-<image>-<slug>` containing one line `<platform> <digest>`.
3. **`publish`** (`needs: [plan, build]`, `if: always() && needs.plan.result == 'success' && needs.plan.outputs.has_releases == 'true'`; matrix `include: fromJson(needs.plan.outputs.plans)`; `fail-fast: false`; permissions `contents: write, packages: write, id-token: write, attestations: write`):
   - Steps gated `if: matrix.mode == 'build'`:
     - download artifacts `digest-<image>-*` (merge-multiple); fail if the count ≠ number of platforms from `read_platforms` (protects against partial builds of this image while letting other images publish).
     - `docker buildx imagetools create --dry-run` from the per-arch digests with `--annotation "index:org.opencontainers.image.description=$(ci.py description --image <image>)"` plus `source`, `revision`, `version`, `created` index annotations → `index.json`.
     - push untagged: `oras manifest push --media-type application/vnd.oci.image.index.v1+json ${REGISTRY}/<image> index.json` (oras setup action pinned); `DIGEST=sha256:$(sha256sum index.json | cut -d' ' -f1)`; verify `crane digest ${REGISTRY}/<image>@$DIGEST` equals it.
     - `sigstore/cosign-installer` (reuse sha) → `cosign sign --yes --recursive ${REGISTRY}/<image>@$DIGEST`.
     - `actions/attest-build-provenance` on the index digest, `push-to-registry: false`.
     - tag last: `oras tag ${REGISTRY}/<image>@$DIGEST X.Y.Z X.Y X latest`.
   - Always (both modes): `ci.py release-notes … --out notes.md --sbom-dir sboms`, then `gh release create "<image>/v<version>" --target ${{ github.sha }} --title "<image> <version>" --notes-file notes.md --latest=false sboms/*` (creates the git tag last).
4. **`cleanup`** (`needs: publish`, `if: always() && needs.plan.outputs.has_releases == 'true'`, `uses: ./.github/workflows/cleanup.yml`, `with: dry-run: false`, `permissions: contents: write, packages: write`).

- [ ] **Step 1: Rewrite the workflow.**
- [ ] **Step 2: Run actionlint, expect no errors; run unit tests, expect PASS.**
- [ ] **Step 3: Dry-check the plan step locally:** `python3 .github/scripts/ci.py release-plan --head-sha $(git rev-parse HEAD)`. Expected: four plans, all `1.0.0`, mode `build` (no `X.Y.Z` tags exist yet; `crane` reports not found).
- [ ] **Step 4: Commit** — `feat(ci): publish tested digests with semver, signatures and attestations`

---

### Task 10: Renovate + README

**Files:**
- Modify: `renovate.json` — add `"customManagers:githubActionsVersions"` to `extends` (matches the `# renovate:` comment above `CST_VERSION`). Nothing else changes.
- Modify: `README.md`

README content (replace "Manual build" heading position, keep the manual build and platforms sections):
- Image table: name, description (the label text), platforms, links to `https://github.com/orgs/bill-of-materials/packages/container/package/<image>` and `https://github.com/bill-of-materials/factory/releases?q=<image>`.
- "Versioning": per-image semver, bump rules (major/minor/patch as in Global Constraints), tags `X.Y.Z`/`X.Y`/`X`/`latest`, first release `1.0.0`, `.md`-only changes don't release.
- "Verify an image": the `cosign verify` and `gh attestation verify` commands from the notes template with `<image>`/`<digest>` placeholders.
- "Retention": last 10 releases per image; untagged leftovers removed automatically after each release and weekly; removed versions keep their GitHub Release with a note.
- "Setup notes": per package → Package settings → Manage Actions access → `bill-of-materials/factory` with **Admin** role (untick “Inherit access from source repository” if needed); visibility Public; required check `validation-required`.
- Updated mermaid: PR → build per arch → structure tests; main → build per arch (push by digest) → test digest → SBOM + provenance attestations → multi-arch index → cosign sign (recursive) → tags → GitHub Release → cleanup.

- [ ] **Step 1: Edit both files.**
- [ ] **Step 2: Validate Renovate config:** `docker run --rm -v "$PWD:/repo" -w /repo renovate/renovate:41 renovate-config-validator renovate.json` → `Config validated successfully`.
- [ ] **Step 3: Commit** — `doc: document versioning, verification and retention`

---

### Task 11: End-to-end verification on GitHub (needs owner go-ahead)

Pushing the branch, opening the PR and merging are outward-facing: **ask the owner before each**. The owner must have done the "Manage Actions access → Admin" setup before the merge.

- [ ] **Step 1:** Push `ci/multiarch-release-pipeline`, open a PR. Because Task 3 touched all four Dockerfiles, `validate` runs every image × platform. Expected: all `structure-test (…)` jobs green, including `linux/arm/v7`; `validation-required` green. Fix and repeat on failure (use superpowers:systematic-debugging).
- [ ] **Step 2:** After owner merges, watch `release` (`gh run watch`). Expected: four releases `alpine3/v1.0.0`, `debian13-slim/v1.0.0`, `ninjam/v1.0.0`, `snapcast/v1.0.0`; cleanup job ran.
- [ ] **Step 3: Verify every promise from "Done means"** for one image (ninjam) and spot-check the others:
  - `crane ls ghcr.io/bill-of-materials/ninjam` → `1.0.0 1.0 1 latest` + `sha256-…` attachment tags, no 40-char sha tags
  - `docker buildx imagetools inspect ghcr.io/bill-of-materials/ninjam:1.0.0` → only real platforms, no `unknown/unknown`
  - every command in the release notes runs successfully as written (pull, `cosign verify` on the index and on each per-arch digest, `gh attestation verify`)
  - package page shows the description
  - cleanup logs show legacy versions deleted, including `alpine3@sha256:9eee99e…`
  - Renovate opens the PR moving `ninjam`/`snapcast` to the new `alpine3` digest; tell the owner it needs approving (their 1.0.1 releases follow)
- [ ] **Step 4:** Report results to the owner, including anything that didn't match.
