"""Archive layout: repo id in, paths and layout verdicts out (ADR 0003).

One directory per source repo. A model directory's path mirrors the
Hugging Face repo id verbatim — ``models/<owner>/<repo>`` — so the
destination of a pull is a pure function of the id the human typed, with
no inference, no prompt, and no hub metadata call (spec 0017).

This module owns three rules, so that verify, migrate, and pull cannot
drift from each other:

- how a repo id becomes a path under ``models/`` and ``.staging/``
- how a repo id becomes the ``source_repo`` URL a record carries, and
  back again
- whether a model directory satisfies ADR 0003's three-way invariant:
  ``directory path == record.hub_id == every artifact's source_repo``

``pull_record``, ``pull_prepare`` and ``cli.model_errors`` route
through it. One deliberate holdout remains: ``pull_advisory`` parses
``source_repo`` with a laxer rule of its own (no host check, no
component validation), and routing it here would change which repos
appear in its advisories — a behavior change that belongs with the pull
work in pass 3, not with this seam.

The invariant is what makes a directory name checkable. Under ADR 0001's
canonical grouping no such relation existed — a directory could be named
for a model none of its files came from, and nothing could say so.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from llm_preserver.records import ID_COMPONENT_RE, ModelRecord

HUB_URL_PREFIX = "https://huggingface.co/"
"""Hub URL stem that ``source_repo`` values are written with."""

MODELS_DIRNAME = "models"
STAGING_DIRNAME = ".staging"

LayoutState = Literal["ok", "unmigrated"]
"""``ok`` when the three-way invariant holds; ``unmigrated`` when not."""


@dataclass(frozen=True)
class LayoutVerdict:
    """One model directory's layout judgment.

    Attributes:
        state: ``ok`` or ``unmigrated``.
        offending_repo: The repo id that contradicts the directory —
            the first artifact source that disagrees, or the record's
            own ``hub_id`` when *that* is what disagrees. Always a
            validated repo id, never a URL and never free text: it is
            what the human types into the remedy command. None when the
            state is ``ok``, and also when the contradicting value is
            not a usable repo id (the state still says ``unmigrated``;
            there is simply no id worth printing).
    """

    state: LayoutState
    offending_repo: str | None = None


def split_repo_id(repo_id: str) -> tuple[str, str]:
    """Split a repo id into its owner and repo components.

    Args:
        repo_id: A hub repo id, ``<owner>/<repo>``.

    Returns:
        The ``(owner, repo)`` pair.

    Raises:
        ValueError: If the id is not exactly two components, each
            matching ``ID_COMPONENT_RE``. Callers map this to their own
            fault domain — a repo id is untrusted input, and no path is
            built from one that fails here.
    """
    owner, separator, repo = repo_id.partition("/")
    if not separator or not ID_COMPONENT_RE.fullmatch(owner) or not ID_COMPONENT_RE.fullmatch(repo):
        raise ValueError(f"repo id must look like <owner>/<repo>, got {repo_id!r}")
    return owner, repo


def model_dir_for(archive_root: Path, repo_id: str) -> Path:
    """The model directory a repo's files are archived in.

    Raises:
        ValueError: If ``repo_id`` is not two valid components.
    """
    owner, repo = split_repo_id(repo_id)
    return archive_root / MODELS_DIRNAME / owner / repo


def staging_dir_for(archive_root: Path, repo_id: str) -> Path:
    """The staging directory a repo's in-flight download lands in.

    Mirrors :func:`model_dir_for` one tree over, so an interrupted pull
    is findable from the same id (spec 0012).

    Raises:
        ValueError: If ``repo_id`` is not two valid components.
    """
    owner, repo = split_repo_id(repo_id)
    return archive_root / STAGING_DIRNAME / owner / repo


def source_repo_url(repo_id: str) -> str:
    """The ``source_repo`` URL recorded for a repo id."""
    return f"{HUB_URL_PREFIX}{repo_id}"


def repo_id_from_url(url: str | None) -> str | None:
    """Read a repo id back out of a recorded ``source_repo``.

    Reading a record is a read path over bytes the tool may not have
    written, so anything that is not a two-component hub repo URL yields
    None rather than raising or half-parsing a path component.

    Args:
        url: A recorded ``source_repo``, or None.

    Returns:
        The ``<owner>/<repo>`` id, or None when the URL is absent,
        malformed, points at another host, or carries extra path
        segments (``/tree/main`` and friends).
    """
    if not url:
        return None
    parsed = urlparse(url)
    if f"{parsed.scheme}://{parsed.netloc}/" != HUB_URL_PREFIX:
        return None
    repo_id = parsed.path.strip("/")
    try:
        split_repo_id(repo_id)
    except ValueError:
        return None
    return repo_id


def layout_state(model_id: str, record: ModelRecord) -> LayoutVerdict:
    """Judge one model directory against the ADR 0003 invariant.

    Record-only: compares the directory's id to the record's ``hub_id``
    and to each artifact's ``source_repo``. No file is opened and
    nothing is hashed, so `verify` reaches this verdict for free.

    An artifact with *no* ``source_repo`` is skipped rather than blamed —
    a missing claim carries no contradiction about where the files
    belong. (``migrate`` refuses on the same input: relocating files
    needs a claim, judging a path does not.) A claim that is *present
    but unparseable* is the opposite case and convicts: the record
    asserts an origin, and one this tool cannot read is one it cannot
    confirm is the directory's own.

    Args:
        model_id: The ``<owner>/<repo>`` the directory's path spells.
        record: The record found in that directory.

    Returns:
        The verdict, naming the first repo that contradicts the path.
    """
    if record.hub_id != model_id:
        # hub_id is a plain str on the model with no validator, and this
        # value reaches printed output — run it through the same gate the
        # artifact branch uses so a hostile record cannot inject argv-ish
        # text into a line a human may paste (0007 / 0010 / 0013 class).
        try:
            split_repo_id(record.hub_id)
        except ValueError:
            return LayoutVerdict("unmigrated", None)
        return LayoutVerdict("unmigrated", record.hub_id)
    for artifact in record.artifacts:
        if artifact.source_repo is None:
            # No claim at all carries no contradiction.
            continue
        source_id = repo_id_from_url(artifact.source_repo)
        if source_id is None:
            # Present but unreadable is a different thing from absent:
            # the record asserts an origin this tool cannot confirm is
            # the directory's own, and reporting that as ``ok`` would
            # let a misfiled directory verify clean (adjudicated
            # 2026-08-11). No id worth printing, so none is named.
            return LayoutVerdict("unmigrated", None)
        if source_id != model_id:
            return LayoutVerdict("unmigrated", source_id)
    return LayoutVerdict("ok")


def unmigrated_directories(archive_root: Path) -> list[str]:
    """Model ids whose directory holds another repo's files (ADR 0003).

    Record-only, like :func:`layout_state`: no payload is opened. Used
    by the content gate below and by anything that wants to report the
    conversion backlog.
    """
    from pydantic import ValidationError

    from llm_preserver.archive import iter_model_dirs
    from llm_preserver.records import RECORD_FILENAME, load_record

    offenders: list[str] = []
    for model_id, model_dir in iter_model_dirs(archive_root):
        if not (model_dir / RECORD_FILENAME).is_file():
            continue
        try:
            record = load_record(model_dir)
        except (ValidationError, ValueError, OSError):
            # An unreadable record is verify's finding to report, not
            # this gate's: it says nothing about the layout either way.
            continue
        if layout_state(model_id, record).state == "unmigrated":
            offenders.append(model_id)
    return offenders


class UnmigratedArchiveError(Exception):
    """The archive must be converted before it accepts new content."""


def require_migrated_archive(archive_root: Path) -> None:
    """Refuse to add or relocate content in a pre-ADR-0003 archive.

    Reads and runtime views stay available — ``status``, ``show``,
    ``verify`` and ``views`` all keep working, and ``verify`` still
    refreshes its own manifest. What is gated is *writing new content*:
    a pull into an unconverted archive would create the publisher's
    directory while the same repo's files still sat under another one,
    manufacturing the duplicate-target collision migration would then
    have to merge (spec 0017 criteria 23-25).

    Raises:
        UnmigratedArchiveError: Naming the count and the remedy.
    """
    offenders = unmigrated_directories(archive_root)
    if not offenders:
        return
    count = len(offenders)
    noun = "directory" if count == 1 else "directories"
    raise UnmigratedArchiveError(
        f"this archive has {count} model {noun} holding another repo's files "
        f"(for example {offenders[0]}); convert it first with 'migrate' — "
        "adding content now would put the same repo's files in two places"
    )
