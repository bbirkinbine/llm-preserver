"""How much of the terminal one discover frame may fill (spec 0015).

Sizing a frame to the terminal is what keeps a listing readable on a
console with little or no scrollback — the failure this spec exists to
fix was a 169-line frame whose top could not be scrolled back to.

Two numbers come from here. The **height** is a budget in *lines*, not
rows: ``fit_rows`` spends it on listing rows and on the section labels
between them, so a tree frame showing one relation fits one fewer row
than the budget. The **width** decides how many physical lines each row
actually occupies — real hub ids render 90-98 characters (measured
2026-08-06), which wrap to two rows at 80 columns, and a budget that
counted logical lines would promise a screenful and deliver two
(review round, 2026-08-06).

The ``isatty`` gate is load-bearing rather than stylistic.
``shutil.get_terminal_size`` consults ``LINES``/``COLUMNS`` *before*
asking the OS, verified live 2026-08-06: ``LINES=99`` changes its answer
even when stdout is a pipe. Reading it first would let an exported
``LINES`` on a CI runner or a developer's shell change piped output,
breaking the determinism the non-TTY path promises. Check the stream
first, and a piped run can never consult the environment at all.
"""

import shutil
from typing import TextIO

# Piped runs get a fixed budget so output is byte-identical across
# machines and runs (the 0009 precedent: TTY-only progress rendering,
# non-TTY output unchanged).
NON_TTY_WINDOW_ROWS = 20

# A terminal too short for the chrome still has to page. Five lines is
# small enough to fit almost anything and large enough that "m" makes
# visible progress.
MIN_WINDOW_ROWS = 5


def _interactive(stream: TextIO | None) -> bool:
    """Whether ``stream`` is a terminal, treating a broken one as not.

    CPython sets ``sys.stdout`` to None when fd 1 is invalid at startup
    (``llm-preserver discover q /archive >&-``, some launcher
    contexts), and a closed stream raises on ``isatty``. Click's
    ``echo`` guards for exactly this, so every other line of the
    command degrades quietly; this call runs before any output, so
    without the guard a silent run became a rich traceback instead
    (security review, 2026-08-06 — the 0011/0012 class).
    """
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def resolve_window_size(stream: TextIO | None, chrome_lines: int) -> int:
    """Lines one frame may spend on listing rows and their section labels.

    Args:
        stream: The stream the frame is printed to. Only its
            ``isatty()`` is consulted, and only it decides whether the
            terminal size is read at all.
        chrome_lines: Lines the frame spends on everything that is not
            a listing row — headers, the ancestry ladder, the pull
            line, the footer, the prompt.

    Returns:
        The line budget: a fixed ``NON_TTY_WINDOW_ROWS`` when the
        stream is not a usable terminal, otherwise the terminal height
        less the chrome, never below ``MIN_WINDOW_ROWS``.
    """
    if not _interactive(stream):
        return NON_TTY_WINDOW_ROWS
    return max(MIN_WINDOW_ROWS, shutil.get_terminal_size().lines - chrome_lines)


def resolve_window_width(stream: TextIO | None) -> int | None:
    """Columns available for one line, or None when there is no terminal.

    Args:
        stream: The stream the frame is printed to.

    Returns:
        The terminal width, or None for a piped or broken stream —
        which means "do not wrap-adjust", keeping piped output at a
        flat one line per row and therefore byte-identical.
    """
    if not _interactive(stream):
        return None
    return shutil.get_terminal_size().columns
