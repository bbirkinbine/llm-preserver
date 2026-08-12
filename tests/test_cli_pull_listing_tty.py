"""pull's file listing pages instead of walling — spec 0018, pass 4.

Live-use trigger: ``discover 'kimi k3'`` → ``1 = pick files`` printed
171 file rows into a 24-line terminal, one stage after discover's own
windowed frames. This module holds the **shared harness** for the CLI
contract of the two-frame loop inside ``prompt_for_selection``, plus the
tests for which frame a repo opens on:

- a listing that fits prints exactly as it does today — flat, no key
  line, one prompt (so ``q`` there is a pattern, adjudication 5);
- an overflowing listing on a TTY opens on the directory roll-up, with
  every file one ``f`` away.

The rest of the contract lives in sibling modules that import this
harness (the ``test_cli_discover_paging`` → ``test_cli_discover_flow``
idiom): ``…_paging.py`` (``f``/``m``/``b``/``s``, footer grammar,
completeness), ``…_keys.py`` (keys versus patterns, the two fallbacks,
and the two non-TTY streams — a pipe and a broken one), and
``…_geometry.py`` (the one-screen criterion swept across terminal
sizes). Four modules rather than one because each must stay under the
300-line cap in ``.claude/rules/python-code.md``.

**The terminal is simulated at the resolver seam**, never by faking
``isatty``: ``CliRunner`` and pytest's capture both swap ``sys.stdout``
after any patch of it, so a faked TTY measures the piped path by
accident (the idiom is
``tests/test_cli_discover_tty_frames.py:44``). That makes two
requirements of the implementation, both deliberate:
``prompt_for_selection`` reads ``sys.stdout`` *at call time* and asks
``is_interactive`` / ``resolve_window_size`` / ``resolve_window_width``
— imported into ``pull_exec.prompts`` — for the verdict and the budget.

Frames are captured by reading ``capsys`` from inside the patched
``typer.prompt``: everything printed since the previous prompt is the
frame the human was looking at when they answered. The prompt line
itself is *not* in the captured frame — click has not rendered it yet —
so anything measuring frame height charges ``prompt_cost`` separately.

One thing is deliberately left free, because the spec's
``## User-visible strings`` does not pin it: *which* header line the
expanded frames carry (the flat ``files in {repo_id}:`` or the roll-up's
counted form). Only "a header naming the repo is there, and the frame
still fits" is required. Everything else — footer grammar, key lines,
row shape, ordering — is pinned verbatim.
"""

import re

import click
import typer

import llm_preserver.cli.pull_exec.prompts as prompts
from llm_preserver.cli.pull_exec.prompts import prompt_for_selection
from llm_preserver.cli.window import MIN_WINDOW_ROWS, NON_TTY_WINDOW_ROWS
from llm_preserver.hub import RepoFile, RepoInfo
from llm_preserver.pull_preflight import human_size
from llm_preserver.text_window import wrapped_height

COLUMNS = 80
ROWS = 24
REPO_ID = "unsloth/Kimi-K3-Thinking-GGUF"

# Verbatim from prompts.py:50 — the prompt text is unchanged by 0018.
PATTERN_PROMPT = "files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*)"

ROLLUP_KEYS = "f = list every file (paged), q = quit"
FIRST_WINDOW_KEYS = "m = more, s = summary, q = quit"
LATER_WINDOW_KEYS = "m = more, b = back a page, s = summary, q = quit"
NO_ROLLUP_KEYS = "m = more, q = quit"
MORE = " — more (m)"
BACK = " · back (b)"
FOOTER_RE = re.compile(r"showing (\d+)-(\d+) of (\d+)")

# The reproduction case: eight quant directories, 166 shards, five root
# files — 171 rows, the number that walled the live run.
QUANT_DIRS = {
    "UD-IQ1_M": 21,
    "UD-IQ2_XXS": 21,
    "UD-Q2_K_XL": 21,
    "UD-Q3_K_XL": 21,
    "UD-Q4_K_XL": 21,
    "UD-Q5_K_XL": 21,
    "UD-Q6_K_XL": 20,
    "UD-Q8_K_XL": 20,
}
MODEL = "Kimi-K3-Thinking-Instruct-2609"
LEADING_ROOT_FILES = (".gitattributes", "README.md")
TRAILING_ROOT_FILES = ("mmproj-BF16.gguf", "mmproj-F16.gguf", "mmproj-F32.gguf")
TOTAL_FILES = sum(QUANT_DIRS.values()) + len(LEADING_ROOT_FILES) + len(TRAILING_ROOT_FILES)

# Adjudication 4's second arm: enough directories that the roll-up
# overflows in its own right, so there is no roll-up frame to offer.
MANY_DIRS = 200
FILES_PER_DIR = 2


def prompt_cost(width: int) -> int:
    """Physical rows click's rendered prompt occupies at ``width``."""
    return wrapped_height(f"{PATTERN_PROMPT}: ", width)


def info_for(paths_and_sizes) -> RepoInfo:
    """Repo metadata carrying just the fields the listing reads."""
    return RepoInfo(
        commit="0" * 40,
        files=[RepoFile(path=path, size=size, sha256=None) for path, size in paths_and_sizes],
        base_model=None,
        pipeline_tag=None,
        license=None,
    )


def kimi_repo() -> RepoInfo:
    """171 files whose shard rows provably wrap at 80 columns."""
    entries = [(name, 1024) for name in LEADING_ROOT_FILES]
    for directory, count in QUANT_DIRS.items():
        entries += [
            (f"{directory}/{MODEL}-{directory}-{index:05d}-of-{count:05d}.gguf", 19851335840)
            for index in range(1, count + 1)
        ]
    entries += [(name, 1073741824) for name in TRAILING_ROOT_FILES]
    return info_for(entries)


def flat_root_repo() -> RepoInfo:
    """171 files, no subdirectory anywhere — the roll-up has nothing to say."""
    return info_for(
        [
            (f"{MODEL}-UD-Q4_K_XL-{index:05d}-of-{TOTAL_FILES:05d}.gguf", 19851335840)
            for index in range(1, TOTAL_FILES + 1)
        ]
    )


def many_dirs_repo() -> RepoInfo:
    """400 files in 200 directories — the roll-up itself will not fit."""
    return info_for(
        [
            (f"quant-{directory:03d}/shard-{index:05d}.gguf", 1024)
            for directory in range(MANY_DIRS)
            for index in range(1, FILES_PER_DIR + 1)
        ]
    )


def small_repo() -> RepoInfo:
    """Six short rows — no wall, so no window and no keys."""
    return info_for(
        [
            ("README.md", 1024),
            ("config.json", 512),
            ("tokenizer.json", 2048),
            ("tiny-chat-Q4_K_M.gguf", 4096),
            ("tiny-chat-Q8_0.gguf", 8192),
            ("mmproj-F16.gguf", 2048),
        ]
    )


class Prompter:
    """Answers each pattern prompt, keeping the frame that preceded it."""

    def __init__(self, capsys, answer, limit: int = 100) -> None:
        self._capsys = capsys
        self._answer = answer
        self._limit = limit
        self.frames: list[str] = []
        self.texts: list[str] = []

    def __call__(self, text, *args, **kwargs) -> str:
        self.frames.append(click.unstyle(self._capsys.readouterr().out))
        self.texts.append(click.unstyle(str(text)))
        assert len(self.frames) < self._limit, "the listing loop never reached a pattern"
        return self._answer(self.frames)

    @property
    def calls(self) -> int:
        return len(self.frames)


def scripted(*answers):
    """Answer a fixed script, failing loudly if the loop wants more."""
    queue = list(answers)

    def answer(frames):
        assert queue, f"the loop prompted {len(frames)} times; the script has {len(answers)}"
        return queue.pop(0)

    return answer


def walk_all(final: str = "*.gguf"):
    """Leave the roll-up, then press ``m`` to the end of the listing.

    Keyed off what the frame actually offers rather than off a fixed
    script, so the same driver walks a repo that opens on the roll-up
    and one that opens straight onto the paged listing.
    """

    def answer(frames):
        if MORE in frames[-1]:
            return "m"
        if ROLLUP_KEYS in frames[-1]:
            return "f"
        return final

    return answer


def run_listing(
    monkeypatch,
    capsys,
    info,
    answer,
    *,
    repo_id=REPO_ID,
    tty=True,
    columns=COLUMNS,
    rows=ROWS,
    limit=100,
):
    """Drive ``prompt_for_selection`` against a simulated terminal."""
    prompter = Prompter(capsys, answer, limit=limit)
    monkeypatch.setattr(typer, "prompt", prompter)
    monkeypatch.setattr(prompts, "is_interactive", lambda stream: tty)
    if tty:
        monkeypatch.setattr(prompts, "resolve_window_width", lambda stream: columns)
        monkeypatch.setattr(
            prompts,
            "resolve_window_size",
            lambda stream, chrome: max(MIN_WINDOW_ROWS, rows - chrome),
        )
    else:
        monkeypatch.setattr(prompts, "resolve_window_width", lambda stream: None)
        monkeypatch.setattr(
            prompts, "resolve_window_size", lambda stream, chrome: NON_TTY_WINDOW_ROWS
        )
    return prompt_for_selection(info, repo_id), prompter


def physical_height(frame: str, width: int = COLUMNS) -> int:
    """Rows a frame occupies once its lines wrap at ``width``."""
    return sum(wrapped_height(line, width) for line in frame.splitlines() if line.strip())


def paths_in(frame: str, info: RepoInfo) -> list[str]:
    """Which of the repo's files this frame lists individually."""
    return [repo_file.path for repo_file in info.files if repo_file.path in frame]


# --- the listing that fits is the listing that shipped ------------------


def test_a_fitting_listing_prints_the_flat_frame_with_no_keys(monkeypatch, capsys):
    info = small_repo()

    patterns, prompter = run_listing(monkeypatch, capsys, info, scripted("*Q4_K_M*"))

    assert patterns == ["*Q4_K_M*"]
    assert prompter.calls == 1
    frame = prompter.frames[0]
    assert frame.splitlines()[0] == f"files in {REPO_ID}:"
    assert paths_in(frame, info) == [repo_file.path for repo_file in info.files]
    # No key line at all, not merely no "f" — and no window furniture.
    assert "q = quit" not in frame
    assert "f = list every file" not in frame
    assert "showing" not in frame
    assert prompter.texts[0] == PATTERN_PROMPT


# --- the roll-up frame --------------------------------------------------


def test_an_overflowing_listing_opens_on_the_directory_rollup(monkeypatch, capsys):
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("*Q4_K_XL*"))

    frame = prompter.frames[0]
    total = human_size(sum(repo_file.size for repo_file in info.files))
    assert frame.splitlines()[0] == f"files in {REPO_ID} ({TOTAL_FILES} files, {total}):"
    for directory, count in QUANT_DIRS.items():
        assert f"{directory}/" in frame
        assert f"{count} files" in frame
    for name in LEADING_ROOT_FILES + TRAILING_ROOT_FILES:
        assert name in frame
    # Summarized, not paged: no individual shard row here.
    assert f"{MODEL}-UD-Q4_K_XL-00001" not in frame
    assert ROLLUP_KEYS in frame
