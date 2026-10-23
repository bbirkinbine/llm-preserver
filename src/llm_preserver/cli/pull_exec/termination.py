"""Turning SIGTERM and SIGHUP into an unwind while a pull runs (spec 0025).

Python runs no ``finally`` on a signal it does not handle, so a plain
``kill`` or a closed terminal or SSH session used to end a pull with its
claim marker still on disk. From the same host the next pull clears it;
from any other host it blocks every pull of that repo until a human
deletes the file. While :func:`unwind_on_termination` is active, both
signals raise :class:`Terminated` instead, which unwinds exactly like
Ctrl-C: every ``finally`` runs, the claim is released, and staged bytes
stay for the resume.

``kill -9`` cannot be caught by anything, and still leaves the marker.
"""

import contextlib
import signal
import threading
from collections.abc import Iterator
from types import FrameType

# SIGHUP does not exist on Windows; getattr keeps mypy and the module
# importable everywhere (the platform rule in python-code.md).
_SIGNALS = tuple(
    sig for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if sig
)


class Terminated(KeyboardInterrupt):
    """SIGTERM or SIGHUP arrived; a ``KeyboardInterrupt`` so it unwinds like one.

    Attributes:
        signum: The signal that arrived.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextlib.contextmanager
def unwind_on_termination() -> Iterator[None]:
    """Raise :class:`Terminated` on SIGTERM / SIGHUP for the body of the ``with``.

    The previous handlers come back on exit. Outside the main thread
    Python cannot install a handler, so the body runs unchanged.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def raise_terminated(signum: int, frame: FrameType | None) -> None:
        raise Terminated(signum)

    previous = {sig: signal.signal(sig, raise_terminated) for sig in _SIGNALS}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
