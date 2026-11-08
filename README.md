# Factory

BOM OCI-image library and builder

## Images

| Image | Description | Application | Platforms | Links |
| --- | --- | --- | --- | --- |
| `alpine3` | Hardened Alpine 3 base image with a non-root app user, dumb-init, curl and CA certificates. | — | amd64, arm64, arm/v7 | [Package](https://github.com/orgs/bill-of-materials/packages/container/package/alpine3), [Releases](https://github.com/bill-of-materials/factory/releases?q=alpine3) |
| `debian13-slim` | Hardened Debian 13 (trixie) slim base image with a non-root app user, dumb-init, curl and CA certificates. | — | amd64, arm64, arm/v7 | [Package](https://github.com/orgs/bill-of-materials/packages/container/package/debian13-slim), [Releases](https://github.com/bill-of-materials/factory/releases?q=debian13-slim) |
| `ninjam` | NINJAM server (ninjamsrv) built from source on the alpine3 base image. | ninjam <!-- renovate: datasource=git-refs depName=https://github.com/justinfrankel/ninjam branch=main digest=f4c0eff3a1d8d5f3ead470d0c7405c3ee37da24e -->`f4c0eff` | amd64 | [Package](https://github.com/orgs/bill-of-materials/packages/container/package/ninjam), [Releases](https://github.com/bill-of-materials/factory/releases?q=ninjam) |
| `snapcast` | Snapcast server (snapserver) on the alpine3 base image. | snapcast <!-- renovate: datasource=repology depName=alpine_edge/snapcast -->`0.35.0-r1` | amd64, arm64, arm/v7 | [Package](https://github.com/orgs/bill-of-materials/packages/container/package/snapcast), [Releases](https://github.com/bill-of-materials/factory/releases?q=snapcast) |

Pull with `docker pull ghcr.io/bill-of-materials/<image>:<version>`.

## Versioning

Each image is versioned on its own with semver. Every push to `main` that
changes an image folder releases that image; changes that only touch `.md`
files do not.

The bump is computed from the commits since the previous release:

- major: a `type!:` or `type(scope)!:` subject, or `BREAKING CHANGE:` in the body
- minor: a `feat` commit
- patch: anything else

The first release is `1.0.0`. Each release is tagged `X.Y.Z`, `X.Y`, `X` and
`latest` in the registry, and gets a GitHub Release named `<image>/vX.Y.Z`
with the digests, changelog and SBOMs. A published `X.Y.Z` never moves.

## Verify an image

Every release is signed with cosign (keyless) and carries GitHub provenance and
SBOM attestations. Use the digest from the release notes:

```bash
cosign verify ghcr.io/bill-of-materials/<image>@<digest> \
  --certificate-identity-regexp '^https://github.com/bill-of-materials/factory/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://ghcr.io/bill-of-materials/<image>@<digest> -R bill-of-materials/factory
```

## Retention

Only the last 10 releases of each image are kept in the registry, plus the
versions that `X`, `X.Y` and `latest` point to and anything younger than 24
hours. Cleanup runs after every release and weekly. It can also be started from
the Actions tab (`cleanup`, dry-run by default). A removed version keeps its
GitHub Release, with a note saying it was removed from the registry.

## Dependency updates

Renovate keeps every pinned version in this repository up to date: base
images and actions (by digest), workflow runner labels, the build runners in
`.github/scripts/factory_ci/images.py`, `docker run` images in workflows, and
tool versions in workflow `env` (`# renovate:` comment above a `*_VERSION`).

To pin something new in a Dockerfile, annotate the `ARG`:

```dockerfile
# renovate: datasource=repology depName=alpine_edge/<package>
ARG <NAME>_VERSION=1.2.3-r0
# renovate: datasource=git-refs depName=https://github.com/<owner>/<repo> branch=main
ARG <NAME>_REV=<full 40-character commit sha>
```

An annotated `ARG` is the image's application version: it is shown in the
package description, the release notes and the **Application** column above.
When you add one, copy the matching cell into the table (the
`<!-- renovate: ... -->` comment is what Renovate updates); the unit tests fail
if the table and the Dockerfiles disagree. To print the expected cell:

```bash
python3 .github/scripts/ci.py readme-cell --image <image>
```

## Setup notes

- For each package: Package settings, Manage Actions access, add
  `bill-of-materials/factory` with the **Admin** role (untick "Inherit access
  from source repository" if needed).
- Package visibility: Public.
- Required status check: `validation-required`.

## How it works

```mermaid
flowchart TD
    subgraph PR["Pull request (validate)"]
        direction LR
        U["CI unit tests + actionlint"]
        PB["Build each changed image<br/>for each of its platforms"] --> PT["Structure tests"]
    end
    subgraph Main["Push to main (release)"]
        direction TD
        B["Build each platform<br/>(native amd64/arm64, QEMU arm/v7)<br/>push by digest"]
        T["Test pushed digest"]
        A["SBOM + provenance attestations"]
        I["Multi-arch index"]
        S["Cosign sign (recursive)<br/>+ index provenance"]
        G["Tags X.Y.Z, X.Y, X, latest"]
        R["GitHub Release"]
        C["Cleanup"]
        B --> T --> A --> I --> S --> G --> R --> C
    end
```

Pull requests run `validate`: the CI unit tests and actionlint, then a build
and structure test for every platform of each image the PR changes. The
`validation-required` check sums them up. Images are only published by the
`release` workflow on `main`; nothing is pushed from a pull request or by hand.

## Platforms

By default every image is built for `linux/amd64`, `linux/arm64` and
`linux/arm/v7`. To change that for one image, add a `platforms.txt` next to its
`Dockerfile`, one platform per line (commas also work):

```txt
linux/amd64
linux/arm64
```

amd64 and arm64 are built and tested on native GitHub runners, any other
platform under QEMU.

## Adding an image

Create a folder at the repository root containing:

- `Dockerfile`, with an `org.opencontainers.image.description` label (used for
  the package description; CI fails without it),
- `structure-test.yaml`, the [container-structure-test](https://github.com/GoogleContainerTools/container-structure-test)
  checks run on every platform,
- optionally `platforms.txt` (see above) and annotated application `ARG`s (see
  [Dependency updates](#dependency-updates)).

Then add its row to the [Images](#images) table (the unit tests check that every
image has one). Merging to `main` releases it as `1.0.0`. Do the
[setup](#setup-notes) on its package once it exists.

## Build and test locally

The same commands as the `validate` workflow, from the repository root:

```bash
image=snapcast platform=linux/arm64
docker buildx build --platform "$platform" --load -t "local/$image:test" "$image"
container-structure-test test --platform "$platform" \
  --image "local/$image:test" --config "$image/structure-test.yaml"
```

Building for another architecture than your machine's needs QEMU
(`docker run --privileged --rm tonistiigi/binfmt --install all`). The
`IMAGE_SOURCE`, `IMAGE_REVISION` and `IMAGE_CREATED` build arguments are set by
the release workflow and can be left out locally.

The CI scripts' unit tests:

```bash
(cd .github/scripts && python3 -m unittest discover -s tests -t .)
```
