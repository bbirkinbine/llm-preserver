"""Render a pull preparation as a printable dry-run report (spec 0005).

Pure formatting: a ``PullPreparation`` plus the recorded would-ask
prompts in, printable lines out. The CLI echoes them; nothing here
performs I/O. Unlike the size confirmation (counts only — listing 500
shards in a prompt is noise), the plan report is exactly the place
for the itemized view: per-file sizes are what catch a
subtly-wrong ``--include`` pattern before bytes move.
"""

from collections.abc import Sequence

from llm_preserver.pull_preflight import human_size
from llm_preserver.pull_prepare import PullPreparation
from llm_preserver.render import clean_text
from llm_preserver.selection import is_doc_file


def render_plan(prep: PullPreparation, would_ask: Sequence[str]) -> list[str]:
    """Compose the ``--plan`` report lines for a prepared pull.

    Args:
        prep: The pull's decision state from ``prepare_pull``.
        would_ask: Prompts a real pull would have asked, in the order
            they were recorded by the plan-mode confirm callback.

    Returns:
        Printable report lines, ending with the nothing-happened
        closing line.
    """
    to_download = {planned.repo_file.path for planned in prep.plan.to_download}
    lines = [f"plan: pull from {prep.repo_id} into {prep.model_dir}"]
    for repo_file in prep.selected:
        size = "?" if repo_file.size is None else human_size(repo_file.size)
        if repo_file.path not in to_download:
            marker = "  — already archived"
        elif is_doc_file(repo_file.path):
            marker = "  — doc, rides along"
        else:
            marker = ""
        # Hub-supplied paths are untrusted; strip control characters
        # before they reach a terminal.
        lines.append(f"  {size:>10}  {clean_text(repo_file.path, single_line=True)}{marker}")
    lines.append(
        f"total to download: {human_size(prep.needed_bytes)} "
        f"({len(prep.plan.to_download)} of {len(prep.selected)} files)"
    )
    if prep.needed_bytes > prep.disk_free:
        lines.append(
            f"disk preflight: insufficient space — {human_size(prep.needed_bytes)} needed, "
            f"{human_size(prep.disk_free)} available"
        )
    else:
        lines.append(f"disk preflight: ok ({human_size(prep.disk_free)} free)")
    if prep.adapter_config_fetched:
        # The one adjudicated exception to "downloads nothing" — the
        # report must own it, not just a stderr log line.
        lines.append("fetched adapter_config.json to read its base-model pointer (advisory only)")
    lines.extend(
        f"{advisory.severity}: {clean_text(advisory.message, single_line=True)}"
        for advisory in prep.advisories
    )
    lines.extend(f"would ask: {prompt}" for prompt in would_ask)
    if prep.adapter_config_fetched:
        lines.append("plan only: no weights downloaded, nothing written")
    else:
        lines.append("plan only: nothing downloaded, nothing written")
    return lines
