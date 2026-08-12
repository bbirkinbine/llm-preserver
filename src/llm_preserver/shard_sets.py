"""What counts as a sharded weight set, in one place.

Two features read this convention and must never disagree about it:
``pull_advisory``'s incomplete-set warning, which is what stands
between a partial pull and an archive that cannot load, and pull's
file listing, which rolls a shard set up to one line the way it rolls
up a directory (spec 0018). Extracted here when the listing became the
second consumer.

External references, unchanged from ``pull_advisory``'s header:
HF transformers big-model sharding
(``model-00001-of-00006.safetensors``,
https://huggingface.co/docs/transformers/main/en/big_models,
Apache-2.0) and llama.cpp ``src/llama.cpp``
``SPLIT_PATH_FORMAT = "%s-%05d-of-%05d.gguf"`` (MIT).
"""

import re
from pathlib import PurePosixPath

# model-00001-of-00003.safetensors / tiny-chat-00001-of-00002.gguf
#
# The digit counts are NOT fixed at five. Both cited conventions pad to
# five (HF's docs, llama.cpp's ``SPLIT_PATH_FORMAT``), but real repos
# publish other widths — ``Uniboshi/Kimi-K3-Abliterated-V1`` ships
# ``model-00001-of-000096.safetensors``, six digits in the total —
# and a ``\d{5}`` total silently matched nothing there. Silently is the
# problem: the incomplete-shard-set advisory is what stands between a
# partial pull and an archive that cannot load, and it simply did not
# fire. Measured on that repo, 9 of 96 shards selected: 0 advisories,
# against 1 for the identical selection with five-digit padding
# (live use, 2026-08-12).
#
# ``prefix`` must stay **lazy**. With a greedy ``.+`` and a variable
# ``\d+``, the prefix eats the leading zeros and
# ``model-00001-of-000096`` groups under ``model-0000`` with index
# ``1`` — one set per shard, which is worse than not matching at all.
SHARD_RE = re.compile(r"^(?P<prefix>.+?)-(?P<index>\d+)-of-(?P<total>\d+)(?P<ext>\.[^.]+)$")


def shard_set_key(path: str) -> str | None:
    """Name the shard set ``path`` belongs to, as a typeable pattern.

    Public because two features read the same convention: this module's
    incomplete-set advisory, and pull's file listing, which rolls a
    shard set up to one line the way it rolls up a directory (spec
    0018). One matcher, so the listing can never disagree with the
    advisory about what a set is.

    Args:
        path: A repo-relative file path.

    Returns:
        ``{prefix}-*{ext}`` — a glob that matches the whole set and can
        be pasted at the pattern prompt as-is — or None when the name
        is not a shard.
    """
    match = SHARD_RE.match(PurePosixPath(path).name)
    if match is None:
        return None
    return f"{match['prefix']}-*{match['ext']}"
