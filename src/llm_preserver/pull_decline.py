"""A human's answer to end a pull, kept out of the fault hierarchy.

``PullError`` and its subclasses name *fault domains* — the tool maps
each to a triage exit code (``exit_for_pull_error``). A human who types
``q`` at the file listing, answers ``n`` to a size confirmation, or
presses Ctrl-D at either has not hit a fault: they answered the question
the tool asked. Spec 0021 makes that exit 0 with a plain line, matching
``remove``'s declined confirmation and ``discover``'s own quit prompts.

``PullDeclined`` is deliberately **not** a ``PullError``. Inheriting
from it would put every decline back inside ``PULL_FAULT_DOMAINS``'
isinstance walk, where a future domain could silently reclassify one as
a failure — the exact drift the type split exists to prevent. The
separation is asserted by a test, not merely written down here.

The module imports nothing from this package on purpose: it is imported
by ``pull``, ``pull_prepare``, the CLI prompts, and ``run_pull``, and a
leaf with no imports cannot participate in a cycle.
"""


class PullDeclined(Exception):
    """A human declined or quit at a pull prompt; nothing was pulled.

    The message is the line ``run_pull`` prints to stdout before
    exiting 0, so it reads as a statement of what happened rather than
    an error: ``nothing pulled: <what the human did>``.
    """


# One line per prompt, however the answer arrived. Ctrl-D on a terminal
# is the same answer as a typed "n" or a "q", so both paths raise the
# same string; keeping them here rather than at the raise sites is what
# makes that true by construction instead of by inspection.
QUIT_LINE = "nothing pulled: quit at the file listing"
SIZE_DECLINED = "nothing pulled: size confirmation declined"
EVERY_WEIGHT_DECLINED = "nothing pulled: every-weight pull declined; narrow --include and re-run"
