"""``discover --match-ollama``: digest-verified hub matching (spec 0013).

Reads the model-layer SHA256 from the local Ollama manifest (read-only,
no server), runs the normal hub name search, fetches candidate file
listings (metadata only — nothing is downloaded or hashed), and states
one fact per candidate GGUF: byte-identical to the local blob, or
unverified. Candidates keep the hub's order, the human picks, and the
tool never pulls — a byte-identical match gets a pasteable ``pull``
command instead.

Output shape (live-use adjudication 2026-07-31: the first real run
buried the one actionable line in twenty candidate blocks): repos
without GGUF files roll up into one summary line, and the pasteable
command prints in a footer as the final output — the 0007 stance,
the line to paste sits directly above the next shell prompt.
"""

import typer

from llm_preserver.discover_render import summary_facts
from llm_preserver.hub import HubClientProtocol, PullError
from llm_preserver.hub_discovery import PAGE_SIZE, ModelSummary
from llm_preserver.ollama_match import GgufVerdict, compose_pull_command, match_gguf_files
from llm_preserver.ollama_store import (
    OllamaStoreError,
    local_model_digests,
    locate_store_root,
    parse_ollama_name,
)
from llm_preserver.render import clean_text

# Ceiling for --limit: every candidate costs one hub metadata call,
# and an unbounded fan-out against a rate-limited API helps nobody.
MATCH_LIMIT_MAX = 500

# Names shown on the no-GGUF roll-up line before truncating to a
# count: at --limit 500 the roll-up itself became a wall of 440 names
# (live-use adjudication 2026-07-31) — the entries are non-actionable,
# so the summary is the signal, not the inventory.
_ROLLUP_NAMES_SHOWN = 10


def refuse(message: str) -> typer.Exit:
    """Print a user-input refusal to stderr and return an exit-2."""
    typer.echo(f"error: {clean_text(message, single_line=True)}", err=True)
    return typer.Exit(code=2)


def run_match(
    client: HubClientProtocol,
    raw_name: str,
    search_override: str | None,
    limit: int = PAGE_SIZE,
) -> None:
    """Drive the match scan; raises typer.Exit(2) on user-input errors.

    Search failures propagate as ``PullError`` for the caller's
    fault-domain mapping; a single candidate's metadata failure is
    reported inline and the scan continues (plan decision, approved
    2026-07-31). ``limit`` caps how many search results are checked
    (adjudicated 2026-07-31: the first live run's second true match
    sat beyond the hub's first page).
    """
    try:
        name = parse_ollama_name(raw_name)
        root = locate_store_root(name)
        digests = local_model_digests(name, root)
    except OllamaStoreError as exc:
        raise refuse(str(exc)) from exc
    term = search_override if search_override is not None else name.model
    shown = ", ".join(digests)
    # Disclose which store was read: with two probeable roots, the
    # human must be able to see the pick (adjudicated 2026-07-31).
    typer.echo(f"ollama store: {clean_text(str(root), single_line=True)}")
    typer.echo(
        f"local {clean_text(name.display(), single_line=True)} — model layer sha256: {shown}"
    )
    typer.echo(
        f"checking the first {limit} hub search results for "
        f"'{clean_text(term, single_line=True)}' (the hub's relevance order):"
    )
    pager = client.search_models(term)
    candidates: list[ModelSummary] = []
    while len(candidates) < limit:
        page = pager.next_page()
        if not page:
            break
        candidates.extend(page)
    candidates = candidates[:limit]
    if not candidates:
        typer.echo("no hub search results — try --search with a different term")
        return
    matches: list[tuple[ModelSummary, str]] = []
    no_gguf: list[str] = []
    for candidate in candidates:
        _report_candidate(client, candidate, digests, matches, no_gguf)
    if no_gguf:
        names = ", ".join(no_gguf[:_ROLLUP_NAMES_SHOWN])
        overflow = len(no_gguf) - _ROLLUP_NAMES_SHOWN
        more = f", … and {overflow} more" if overflow > 0 else ""
        typer.echo(
            f"\n{len(no_gguf)} of {len(candidates)} results have no GGUF files: {names}{more}"
        )
    if not matches:
        typer.echo(
            "\nno exact match was verified: none of these files carries the local "
            "digest. Archiving a near-miss (even a same-size file) archives a "
            "different build whose outputs may diverge."
        )
        return
    if len(matches) == 1:
        typer.echo("\n1 byte-identical match — run this to archive it:")
    else:
        # Every match is the same bytes; one pull suffices, and the
        # only reason to prefer a repo is its provenance.
        typer.echo(
            f"\n{len(matches)} byte-identical matches — all the same bytes; "
            "run any ONE to archive it:"
        )
    for summary, filename in matches:
        # The same hub facts the search rows carry (downloads · date ·
        # gated): identical bytes, so provenance is the only thing left
        # to pick by (adjudicated 2026-07-31). Facts, never a ranking.
        shown_repo = clean_text(summary.repo_id, single_line=True)
        typer.echo(f"  {clean_text(f'{shown_repo}{summary_facts(summary)}', single_line=True)}")
        command = compose_pull_command(summary.repo_id, filename)
        if command is not None:
            typer.echo(f"    {command}")
        else:
            # 0007 posture: an id or filename unsafe to echo gets no
            # composed command; the match fact still surfaces.
            shown_file = clean_text(filename, single_line=True)
            typer.echo(f"    {shown_file}: no safe command could be composed; pull it by hand")


def _report_candidate(
    client: HubClientProtocol,
    candidate: ModelSummary,
    digests: list[str],
    matches: list[tuple[ModelSummary, str]],
    no_gguf: list[str],
) -> None:
    """Print one candidate's verdict lines; collect matches and no-GGUF repos.

    A repo without GGUF files prints nothing here — it joins the
    roll-up line instead (wall-of-text adjudication 2026-07-31). A
    metadata failure stays inline: an anomaly is worth a row.
    """
    shown_repo = clean_text(candidate.repo_id, single_line=True)
    try:
        info = client.repo_info(candidate.repo_id)
    except PullError as exc:
        typer.echo(f"{shown_repo} — metadata unavailable: {clean_text(str(exc), single_line=True)}")
        return
    verdicts = match_gguf_files(digests, info.files)
    if not verdicts:
        no_gguf.append(shown_repo)
        return
    typer.echo(shown_repo)
    for verdict in verdicts:
        typer.echo(f"  {_verdict_line(verdict)}")
        if verdict.matched:
            matches.append((candidate, verdict.path))


def _verdict_line(verdict: GgufVerdict) -> str:
    """One fact line: filename, size, and the stated verdict."""
    size = "size unreported" if verdict.size is None else f"{verdict.size} bytes"
    state = "byte-identical to the local model" if verdict.matched else "unverified"
    return f"{clean_text(verdict.path, single_line=True)}  {size}  {state}"
