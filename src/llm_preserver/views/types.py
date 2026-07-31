"""Shared types for runtime views (spec 0002).

Dataclasses passed between the source scan, the dest preflight, and
the per-tool adapters, plus the one exception type and the marker
filename that identifies a generated view tree.
"""

from dataclasses import dataclass, field
from pathlib import Path

VIEW_MARKER_FILENAME = "llm-preserver-view.json"
"""Root marker of a generated view tree.

Its presence is how the tool recognizes a dest it owns (a non-empty
dest without it is refused untouched) and how a human recognizes the
tree as disposable: deleting a marked tree loses nothing.
"""


class ViewError(Exception):
    """A view cannot be built as requested (bad dest, unknown tool)."""


@dataclass(frozen=True)
class ViewSourceFile:
    """One archived file a view may link.

    Attributes:
        path: Absolute path to the file inside the archive.
        sha256: Lowercase hex digest, verbatim from the record (never
            recomputed from payload bytes).
        size: Recorded size in bytes, or None when the record has none.
    """

    path: Path
    sha256: str
    size: int | None


@dataclass(frozen=True)
class ViewSourceSkip:
    """One file (or record-level problem) excluded from the view.

    Attributes:
        path: The file path (relative, as recorded) or record filename.
        reason: Why it was skipped — always reported, never silent.
        kind: Display class — ``format`` (wrong artifact format for
            this tool), ``companion`` (expected non-payload rider like
            a README; suppressed from the CLI breakdown), or
            ``problem`` (something worth a human look: unhashed,
            sharded, symlink, malformed record).
    """

    path: str
    reason: str
    kind: str = "problem"


@dataclass
class ModelViewSources:
    """Scan result for one model directory.

    Attributes:
        model_id: ``<creator>/<model>`` as laid out on disk.
        eligible: Files a view may link.
        skips: Everything excluded, each with a reason.
    """

    model_id: str
    eligible: list[ViewSourceFile] = field(default_factory=list)
    skips: list[ViewSourceSkip] = field(default_factory=list)


@dataclass
class ViewSourceScan:
    """Whole-archive scan: one entry per model directory."""

    models: list[ModelViewSources] = field(default_factory=list)


@dataclass(frozen=True)
class ViewEntry:
    """One model registration the seeded view offers to Ollama.

    Attributes:
        name: Deterministically minted model name (``creator/model:tag``).
        model_id: The archive model the entry came from.
        blob_path: The ``blobs/sha256-<digest>`` symlink in the view.
        manifest_path: The synthesized manifest registering the model.
        config_digest: SHA256 of the synthesized config blob (hex).
    """

    name: str
    model_id: str
    blob_path: Path
    manifest_path: Path
    config_digest: str


@dataclass
class ViewBuildResult:
    """What ``build_view`` produced.

    Attributes:
        entries: Seeded registrations (empty in instructions-only mode
            and when no model is eligible).
        scan: The source scan behind the build, for reporting.
        instructions: Human-facing next steps (serve/create commands).
    """

    entries: list[ViewEntry]
    scan: ViewSourceScan
    instructions: str
