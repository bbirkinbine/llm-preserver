"""Rendering for pull's interactive file listing (spec 0018): data → lines.

Pure by design: no terminal, no ``typer``, no I/O. The loop in
``prompts`` decides *which* frame to print and asks ``cli.window`` how
much room it has; everything about what a frame says lives here, where
one pytest can check the arithmetic without a TTY.

The listing exists to answer one question — which glob to type — and
the fact that answers it is *which groups exist*, not the individual
shard names inside them. So an overflowing repo gets a roll-up: one
line per top-level directory, one per sharded weight set at the root,
each with its file count and total size, and everything else listed
individually.

Both group kinds came from live use. Directories are how a quant repo
is laid out (`UD-Q4_K_XL/`, 32 shards); a full-weights HF snapshot has
no directories at all and is 96 files of
``model-NNNNN-of-NNNNNN.safetensors``, where directory grouping had
nothing to say and the human paged the wall anyway (2026-08-12). The
shard convention is not this module's to define — ``shard_sets`` owns
it, shared with the advisory that warns about incomplete sets, so the
listing and the warning can never disagree about what a set is.

The roll-up summarizes and never replaces; every file stays reachable
behind ``f``, and nothing here filters, ranks, or reorders. Groups
appear in the slot of their first member because hub order is a
standing invariant (0000, 0006) — a listing that quietly sorted would
be the tool having an opinion.

The counts and sizes are sums of exactly what is there. Where the hub
reports no size, the total says ``at least`` rather than pretending the
missing bytes are zero.
"""

import fnmatch
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from llm_preserver.hub import RepoFile
from llm_preserver.pull_advisory import COMPANION_RULES
from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text
from llm_preserver.shard_sets import shard_set_key
from llm_preserver.text_window import wrapped_height

# Column the file count trails at on a roll-up line, and the floor for
# it. A directory name longer than this widens the column for the whole
# listing rather than being truncated — a truncated name is not a
# pattern anyone can type, and a name that runs flush into its own
# count is not readable either (review round, 2026-08-12: a 40-char
# name rendered `AAAA…AAAA/1 files`).
_NAME_PAD = 26

# Gap between the longest directory name and the count column.
_NAME_GAP = 2

# Width of the size column, matching the flat listing that shipped.
_SIZE_PAD = 10


@dataclass(frozen=True)
class ListingGroup:
    """One roll-up entry: a top-level directory, or a single root file.

    Attributes:
        name: The directory name without its slash, or the root file's
            path.
        is_directory: Whether this group summarizes a directory. A lone
            root file renders as itself, so it carries no count.
        is_shard_set: Whether this group summarizes a ``-NNNNN-of-NNNNN``
            weight set at the repo root. Its ``name`` is already a
            typeable glob (``model-*.safetensors``), not a bare name.
        file_count: Files beneath this group, at any depth.
        total_size: Sum of the sizes the hub reports. A floor, not a
            claim, when ``has_unknown_size`` is set.
        has_unknown_size: Whether any member's size is None.
    """

    name: str
    is_directory: bool
    file_count: int
    total_size: int
    has_unknown_size: bool
    is_shard_set: bool = False


def kind_note(path: str) -> str:
    """Annotate a recognized companion kind (the advisory rules table).

    The same curated data the advisories use, shown where the human is
    actually reading filenames (live-use ask, 2026-07-13: "what is
    imatrix again?").

    Args:
        path: The repo-relative file path.

    Returns:
        The note to append, or an empty string when nothing matches.
    """
    name = PurePosixPath(path).name
    for pattern, kind in COMPANION_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            return f"  — {kind}"
    return ""


def group_files(files: Sequence[RepoFile]) -> list[ListingGroup]:
    """Roll files up by directory, then by shard set, keeping hub order.

    Directory is the outer rule: a quant repo's ``UD-Q4_K_XL/`` already
    collapses to one line, and collapsing again inside it would hide
    the name the human types. Shard grouping therefore applies only at
    the root, which is exactly where a full-weights snapshot needs it.

    Args:
        files: The repo's files, in the order the hub returned them.

    Returns:
        One group per top-level directory, per root shard set, and per
        remaining root file, each in the position of its first member.
        Every file lands in exactly one group.
    """
    # A shard set only earns a line of its own when it has more than one
    # member; a lone `-00001-of-00001` file is just a file, and naming
    # it with a glob would be stranger than showing it.
    shard_counts: Counter[str] = Counter()
    for entry in files:
        if "/" not in entry.path:
            key = shard_set_key(entry.path)
            if key is not None:
                shard_counts[key] += 1

    order: list[str] = []
    members: dict[str, list[RepoFile]] = {}
    nested: dict[str, bool] = {}
    sharded: dict[str, bool] = {}
    for entry in files:
        head, separator, _ = entry.path.partition("/")
        shard_key = None if separator else shard_set_key(entry.path)
        if separator:
            name, is_dir, is_shard = head, True, False
        elif shard_key is not None and shard_counts[shard_key] > 1:
            name, is_dir, is_shard = shard_key, False, True
        else:
            name, is_dir, is_shard = entry.path, False, False
        if name not in members:
            order.append(name)
            members[name] = []
            nested[name] = is_dir
            sharded[name] = is_shard
        members[name].append(entry)
    return [
        ListingGroup(
            name=name,
            is_directory=nested[name],
            file_count=len(members[name]),
            total_size=sum(entry.size or 0 for entry in members[name]),
            has_unknown_size=any(entry.size is None for entry in members[name]),
            is_shard_set=sharded[name],
        )
        for name in order
    ]


def _size_column(size: int | None) -> str:
    """Right-align a size, or a question mark when the hub reports none."""
    return f"{'?' if size is None else human_size(size):>{_SIZE_PAD}}"


def _file_line(path: str, size: int | None) -> str:
    """One per-file row: size, path, and any companion-kind note."""
    return clean_text(f"  {_size_column(size)}  {path}{kind_note(path)}", single_line=True)


def flat_lines(files: Sequence[RepoFile]) -> list[str]:
    """Render every file, one row each, in hub order.

    This is the listing that shipped, byte for byte — it is what a
    piped run prints, what a repo small enough to fit prints, and what
    the expanded frames page through.

    Args:
        files: The repo's files, in hub order.

    Returns:
        One rendered row per file.
    """
    return [_file_line(entry.path, entry.size) for entry in files]


def rollup_lines(groups: Sequence[ListingGroup]) -> list[str]:
    """Render the roll-up: a line per directory, root files as themselves.

    Args:
        groups: Groups from ``group_files``, in hub order.

    Returns:
        One rendered line per group. Directory lines carry no
        companion-kind note — ``COMPANION_RULES`` classifies filenames,
        and inferring a directory's kind from its members is the tool
        judgment this project excludes (spec 0018 non-goal).
    """
    names = {
        group.name: clean_text(
            f"{group.name}/" if group.is_directory else group.name, single_line=True
        )
        for group in groups
        if group.is_directory or group.is_shard_set
    }
    # One column width for the whole listing, so the counts line up
    # with each other whatever the longest name turns out to be.
    pad = max([_NAME_PAD] + [len(name) + _NAME_GAP for name in names.values()])
    lines = []
    for group in groups:
        if not (group.is_directory or group.is_shard_set):
            size = None if group.has_unknown_size else group.total_size
            lines.append(_file_line(group.name, size))
            continue
        partial = group.has_unknown_size and bool(group.total_size)
        size = None if group.has_unknown_size and not group.total_size else group.total_size
        count = f"{group.file_count} file" + ("" if group.file_count == 1 else "s")
        # A directory whose members are *all* sizeless renders "?", but
        # one the hub reports 20 of 21 sizes for was rendering a bare
        # sum indistinguishable from an exact one — and that number is
        # what a human weighs against free disk. The "+" is the line's
        # version of the header's "at least" (review round, 2026-08-12).
        # It costs the two-space gutter one character, so the size
        # column stays aligned with the root-file rows beside it.
        flag = "+" if partial else " "
        lines.append(f"  {_size_column(size)}{flag} {names[group.name]:<{pad}}{count}")
    return lines


def flat_header(repo_id: str) -> str:
    """The header the flat listing has always printed.

    Byte-identical to the shipped line: a piped run and a repo that
    fits must look exactly as they did before this spec.

    Args:
        repo_id: The hub id, which is hub-supplied text and scrubbed
            like every other rendered line.

    Returns:
        The header line.
    """
    return clean_text(f"files in {repo_id}:", single_line=True)


def summary_header(repo_id: str, files: Sequence[RepoFile]) -> str:
    """The header an overflowing listing carries: how much is here in total.

    Args:
        repo_id: The hub repo id, which is hub-supplied text.
        files: Every file in the repo.

    Returns:
        The header, saying ``at least`` when any file's size is
        unreported — a floor is honest where a bare sum would not be.
    """
    known = sum(entry.size or 0 for entry in files)
    total = human_size(known)
    if any(entry.size is None for entry in files):
        total = f"at least {total}"
    return clean_text(f"files in {repo_id} ({len(files)} files, {total}):", single_line=True)


def fits(lines: Sequence[str], budget: int, width: int | None) -> bool:
    """Whether ``lines`` occupy no more than ``budget`` physical rows.

    Args:
        lines: The rendered lines a frame would print.
        budget: Rows available, chrome already deducted.
        width: Terminal columns, or None when there is no terminal —
            in which case every line costs one row and the answer is a
            logical-line count.

    Returns:
        True when the whole listing fits and no window is needed.
    """
    return sum(wrapped_height(line, width) for line in lines) <= budget
