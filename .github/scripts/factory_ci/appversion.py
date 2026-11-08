"""Application versions pinned in a Dockerfile.

An application version is an `ARG <NAME>_VERSION=<version>` or
`ARG <NAME>_REV=<full commit sha>` line directly preceded by a Renovate
annotation, e.g.:

    # renovate: datasource=repology depName=alpine_edge/snapcast
    ARG SNAPCAST_VERSION=0.35.0-r1
    # renovate: datasource=git-refs depName=https://github.com/justinfrankel/ninjam branch=main
    ARG NINJAM_REV=f4c0eff3a1d8d5f3ead470d0c7405c3ee37da24e

The README "Application" cell repeats each pin inside an HTML comment so
Renovate updates both files in the same pull request.
"""
import re
from dataclasses import dataclass

SHORT_REV = 7  # Renovate's newDigestShort length

_PIN = re.compile(
    r"^[ \t]*#[ \t]*renovate:(?P<attrs>[^\n]*)\n"
    r"[ \t]*ARG[ \t]+(?P<name>\w+?)_(?P<kind>VERSION|REV)=(?P<value>\S+)",
    re.MULTILINE,
)
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class AppVersion:
    name: str            # lower-case ARG prefix, e.g. "snapcast"
    value: str           # pinned version, or the full commit sha for a revision
    is_revision: bool
    datasource: str
    dep_name: str
    branch: str | None = None

    @property
    def display(self) -> str:
        return self.value[:SHORT_REV] if self.is_revision else self.value

    def __str__(self) -> str:
        return f"{self.name} {self.display}"


def _attrs(text: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in text.split() if "=" in part)


def app_versions(image: str, dockerfile: str) -> list[AppVersion]:
    """Return the annotated application pins of a Dockerfile, in file order."""
    found = []
    for m in _PIN.finditer(dockerfile):
        arg = f"{m['name']}_{m['kind']}"
        attrs = _attrs(m["attrs"])
        if "datasource" not in attrs or "depName" not in attrs:
            raise ValueError(f"{image}: renovate annotation for {arg} needs datasource= and depName=")
        is_revision = m["kind"] == "REV"
        if is_revision:
            if not _FULL_SHA.fullmatch(m["value"]):
                raise ValueError(f"{image}: {arg} must be a full 40-character commit sha")
            if "branch" not in attrs:
                raise ValueError(f"{image}: renovate annotation for {arg} needs branch=")
        found.append(AppVersion(
            name=m["name"].lower().replace("_", "-"), value=m["value"], is_revision=is_revision,
            datasource=attrs["datasource"], dep_name=attrs["depName"], branch=attrs.get("branch"),
        ))
    return found


def summary(versions: list[AppVersion]) -> str:
    """One-line form: "snapcast 0.35.0-r1, other 1.2"."""
    return ", ".join(str(v) for v in versions)


def readme_cell(versions: list[AppVersion]) -> str:
    """README table cell; the HTML comments are what Renovate updates."""
    if not versions:
        return "—"
    parts = []
    for v in versions:
        if v.is_revision:
            marker = (f"<!-- renovate: datasource={v.datasource} depName={v.dep_name} "
                      f"branch={v.branch} digest={v.value} -->")
        else:
            marker = f"<!-- renovate: datasource={v.datasource} depName={v.dep_name} -->"
        parts.append(f"{v.name} {marker}`{v.display}`")
    return ", ".join(parts)


def source_link(v: AppVersion) -> str | None:
    """Link to the pinned commit when the revision comes from a GitHub repository."""
    if v.is_revision and v.dep_name.startswith("https://github.com/"):
        return f"{v.dep_name.removesuffix('.git')}/commit/{v.value}"
    return None
