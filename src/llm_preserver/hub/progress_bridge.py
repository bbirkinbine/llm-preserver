"""Bridge from the Hugging Face client's progress bar to a byte sink.

``hf_hub_download`` has no byte callback, but it takes a ``tqdm_class``
and drives an instance of it while bytes move (spec 0026). This module
builds such a class whose only job is to forward network bytes to a
:class:`~llm_preserver.hub.types.TransferSink`. It renders nothing:
with ``--progress`` the plain lines are the only progress shown, on a
TTY and off one (criterion 5).

External-reference provenance — huggingface_hub v1.33.0, Apache-2.0,
retrieved 2026-11-06 from
https://raw.githubusercontent.com/huggingface/huggingface_hub/v1.33.0/src/huggingface_hub/file_download.py
and
https://raw.githubusercontent.com/huggingface/huggingface_hub/v1.33.0/src/huggingface_hub/utils/_xet_progress_reporting.py:

- ``hf_hub_download(..., tqdm_class: type[base_tqdm] | None = None)``;
  the docstring says it "overwrites the default behavior for the
  progress bar".
- ``http_get`` looks up ``update_transfer`` with ``getattr`` and calls
  it per network chunk, and with a negative count when a ranged retry
  restarts.
- The Xet reporter looks up ``update_transfer`` on the class with
  ``getattr`` and, when present, also calls ``set_transfer_postfix_str``.
- ``utils/tqdm.py`` ``_create_progress_bar`` builds a class that is not a
  subclass of the client's own ``tqdm`` as ``cls(**kwargs)`` and applies
  none of the client's disable signals, so this class decides alone.

The same handling is present at the 1.26.0 support floor (spec 0022),
checked 2026-11-06. ``update_transfer`` is a duck-typed convention, not
a documented API; ``tests/test_hf_client_progress_contract.py`` drives
the real client at the floor and the lock so a release that drops it
fails CI instead of silently reporting nothing.
"""

from tqdm.auto import tqdm

from llm_preserver.hub.types import TransferSink


def transfer_tqdm_class(sink: TransferSink) -> type[tqdm]:  # type: ignore[type-arg]
    """Build a disabled ``tqdm`` subclass that reports to ``sink``.

    Args:
        sink: Receives every network byte increment the client reports.

    Returns:
        A class to pass as ``hf_hub_download``'s ``tqdm_class``.
    """

    class _TransferBar(tqdm):  # type: ignore[type-arg]
        # tqdm starts its monitor thread in __new__ unless the interval
        # is 0, even for a disabled bar.
        monitor_interval = 0

        # The client passes tqdm's own keyword set (unit, total, desc,
        # position, ...), which differs by code path and release; this
        # bar renders nothing, so it accepts and discards them all.
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(disable=True)

        def update_transfer(self, n: int) -> None:
            # Runs inside the client's download callback (inside hf_xet
            # for Xet files), where an exception could fail a download
            # the archive needs. Progress is never worth that.
            try:
                sink.add_transfer_bytes(n)
            except Exception:
                return

        def set_transfer_postfix_str(self, s: str, refresh: bool = False) -> None:
            return None

    return _TransferBar
