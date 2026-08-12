"""Where a pull lands, and the record already there (ADR 0003).

What remains of ``pull_grouping`` after spec 0017. The grouping itself
is gone: a pull's destination is a pure function of the repo id the
human typed (``layout.model_dir_for``), so there is no proposal to
make, no confirmation to gate it, and no ``--model`` to override it.
What survives is the confirm callback type the pull flow threads
through, and reading whatever record is already in the destination.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.hub import PullUserError
from llm_preserver.records import RECORD_FILENAME, ModelRecord, load_record

ConfirmCallback = Callable[[str], bool]
"""Callback that shows the user a prompt and returns their yes/no."""


def load_existing_record(model_dir: Path) -> ModelRecord | None:
    """Load the model's record if one exists; unreadable is a hard stop.

    Raises:
        PullUserError: If a record file exists but cannot be read —
            pulling on top of an unreadable record risks clobbering it.
    """
    if not (model_dir / RECORD_FILENAME).is_file():
        return None
    try:
        return load_record(model_dir)
    except (ValidationError, ValueError, OSError) as exc:
        raise PullUserError(
            f"existing record in {model_dir} cannot be read ({exc}); "
            "fix or move it before pulling into this model"
        ) from exc
