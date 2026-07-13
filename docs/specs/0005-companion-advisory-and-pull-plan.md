# 0005 — Companion Advisory And Pull Plan

**Status:** shipped
**Last updated:** 2026-07-13

## Goal

Two features that ship together because each is half of the same fix:
selective pulls can silently omit files the archived model needs to
exercise all of its capabilities (live hit 2026-07-12: a gemma
Q4_K_M pull omitted `mmproj-F16.gguf`, the vision projector, until a
human noticed), and there is no way to see what a pull will do before
bytes move.

**Companion-artifact advisory:** a curated rules table — data, not
inference; no LLM in the tool — that maps repo-tree filename patterns
to artifact kinds. When a pull's selection excludes a companion kind
the tree ships, or a machine-readable cross-repo dependency is absent
from the archive, print an advisory naming the exact `--include`
pattern or follow-up pull command. Advisory only; never auto-add.
Initial rules table:

| Pattern (in repo tree) | Artifact kind | Advisory trigger |
| --- | --- | --- |
| `*mmproj*` | vision projector | tree ships it, selection excludes it |
| `*mtp-*` | speculative-decoding head | tree ships it, selection excludes it |
| `*imatrix*` | quantization calibration data | tree ships it, selection excludes it |

Substring patterns on purpose (review adjudication 2026-07-12):
llama.cpp's own downloader classifies companions by substring, and
real repos ship mid-name forms like `<model>-mmproj-f16.gguf`.
| shard suffixes (`*-00001-of-00005*`-style) | sharded weight set | selection includes some but not all shards of a set |
| `adapter_config.json` → `base_model_name_or_path` | adapter's base model (cross-repo) | named base model absent from the archive |
| hub `base_model` metadata on a quant repo | full-precision master (cross-repo) | quant-repo pull, master repo absent from the archive |
| hub `base_model` vs explicit `--model` | grouping mismatch | `--model` given, repo declares a `base_model`, and `--model` matches neither it nor the repo id |

The `base_model` row answers "can I still fine-tune this later?": a
quant-repo pull whose declared original is not archived prints the
exact follow-up (`llm-preserver pull <base-repo> --whole-repo`). The
tool already reads `base_model` for canonical-directory grouping
(spec 0003) — same value, second use. Repos that declare no
`base_model` get no advisory; the tool never guesses names.

The grouping-mismatch row (added 2026-07-12 after a live footgun
during manual verification): an explicit `--model` stays verbatim
curator judgment (spec 0003 — never a prompt, never a block), but
when the repo's declared `base_model` disagrees with it, the tool
has proof in hand that the target directory is probably a
copy-paste slip — a Qwen3-0.6B quant was archived into the
Qwen3.6-35B-A3B model directory with no warning, two lines after an
advisory that named the true base. Silent when `--model` equals the
declared base (the correct quant grouping) or the repo id itself
(the sanctioned derived-model/self grouping); anything else prints
an advisory naming both values. Because this row flags likely
*human error* rather than a missing companion, it carries severity
`warning` (Brian, 2026-07-12): it sorts first in every advisory
list, renders with a distinct `warning:` prefix (highlighted
yellow/bold on a terminal in `--plan` output), and logs at WARNING
level on real pulls, so it can't hide in the advisory wall.
Warning-not-block is load-bearing: legitimate mismatches exist —
verified live 2026-07-12 with a quant repo whose declared
`base_model` is a stale pre-rename id (the hub 307-redirects it to
the model's new home), so a curator passing the *current* canonical
id triggers the warning while being right. Also sanctioned: spec
0004's different-`--model`-home rule for second snapshots, curator
naming schemes, and merge repos with multiple bases. Never promote
this warning to a prompt or a refusal.

**`pull --plan` (dry run):** resolve the repo tree with the one
metadata call, apply the selection and the grouping rules, then print
what *would* happen — selected files with sizes and the total, the
canonical model directory, doc files that ride along,
already-archived skips, the disk-preflight verdict, and any
companion-artifact advisories — and exit without downloading or
writing anything. One adjudicated exception (2026-07-12): when the
tree ships `adapter_config.json`, the plan fetches that small config
file to read its base-model pointer — accuracy beats purity, and the
plan output states the fetch explicitly. The archive itself is never
touched. Turns scripted pulls from "hope the pattern is
right" into "verify, then run." `--plan` composes with all three
selection modes: `--include`, `--whole-repo`, and the interactive
listing (list → type patterns → see the plan → exit), so the listing
flow gains a consequences preview before anything downloads.

The advisory also fires on real (non-`--plan`) pulls, printed before
the confirmation prompt, so interactive use gets the same safety net
(adjudicated 2026-07-12). The division of labor: interactive pulls
preview via the listing + advisories + size confirmation (decline to
walk away); `--plan`'s primary customer is *scripted* pulls, where
`--yes` leaves no moment to inspect — verify once with `--plan`, then
run the same command without it.

Two riders on the same surface (adjudicated 2026-07-12):

- **Rename `--all` → `--whole-repo`.** The flag's scope is the single
  named repo; "all" reads model-centric and misleads twice — on a
  quant repo it pulls every quant (rarely wanted), and it never
  crosses repos to fetch the full-precision master. Rename now, while
  the surface is days old; no deprecation alias. Touches spec 0004's
  documented surface, `docs/cli.md`, and the README.
- **Size confirmation + disk preflight on selective pulls.** Today
  only the whole-tree path states total bytes and checks disk before
  downloading; selective pulls show per-file sizes in the listing but
  never a total. Extend the plan → preflight → confirm sequence to
  every path through `pull` — the sizes are already in the one
  metadata call.

Product context: generalizes the vision-companion advisory from the
[0000 roadmap](0000-product.md) "Later" list; builds on the selection
machinery of [0003](0003-selective-pull.md) and the plan/preflight
flow of [0004](0004-full-snapshot.md).

## Success criteria

- `llm-preserver pull <repo> --include <pat> --plan` prints the plan
  (selected files with per-file sizes and a total, target model
  directory, docs riding along, already-archived skips, preflight
  verdict, advisories) and exits without creating or modifying any
  file under the archive root — verified by comparing an archive tree
  hash before and after.
- `--plan` composes with all three selection modes: `--include`,
  `--whole-repo`, and the interactive listing (the selection prompt
  still runs; the plan prints after patterns are entered). The
  printed plan matches what an immediately following real pull with
  the same selection does.
- `--plan` asks no *confirmation* prompts (the interactive selection
  prompt is input, not confirmation). Plan-affecting questions a real
  pull would ask (grouping confirmation, "selection covers every
  weight?") are resolved to the proceed-enabling answer — the one
  that lets planning continue — and printed as `would ask: ...`
  lines in the output (adjudicated 2026-07-12).
- Exit codes under `--plan`: 0 when the pull would proceed; the
  existing environment-fault exit code when the disk preflight would
  refuse — so a script can gate on it: plan exits 0, then run for
  real (adjudicated 2026-07-12).
- A pull (real or `--plan`) of a selection that excludes an
  `*mmproj*` file present in the tree prints an advisory naming the
  file, its kind, and the exact `--include` addition — reproducing
  the gemma incident in a test and showing it caught.
- Pulling an adapter repo whose `adapter_config.json` names a base
  model absent from the archive prints a follow-up-pull advisory;
  the same pull with the base model already archived prints none.
- Pulling from a quant repo that declares `base_model` prints the
  full-precision-master advisory (naming the exact
  `pull <base-repo> --whole-repo` command) when that repo is absent
  from the archive, and prints none when it is archived or when the
  repo declares no `base_model`.
- A pull (real or `--plan`) with an explicit `--model` that matches
  neither the repo's declared `base_model` nor the repo id prints a
  grouping-mismatch advisory naming both values; `--model` equal to
  the declared base or to the repo id prints none, and the pull
  itself proceeds unchanged either way (advisory only).
- `pull --whole-repo` behaves exactly as `--all` did (spec 0004
  semantics unchanged); `--all` is gone from `--help`, `docs/cli.md`,
  and the README, and passing it fails as an unknown option.
- A selective pull (interactive or `--include`) states the selection's
  total download size in a confirmation before any bytes move, and
  the disk preflight refuses an over-budget selective pull with the
  same environment-fault exit as the whole-tree path.
- A selection covering only part of a shard set triggers the
  incomplete-set advisory.
- Advisories are archive-aware: a same-repo companion (e.g. the
  `mmproj` projector) already archived from an earlier pull produces
  no advisory on a later pull of the same repo.
- Advisories never change the selection: the downloaded file set with
  and without an advisory firing is identical.
- The rules table lives as data (module-level structure or data
  file), not branching logic, so adding a kind is a one-row change.
- `docs/cli.md` documents `--plan` (semantics, exit codes) and the
  advisory (what it checks, that it never auto-adds); README quick
  start mentions `--plan` if the surface warrants it.
- `--plan` requires an initialized archive (already-archived skips
  need the record) and fails with the normal archive error otherwise.

## Non-goals

- Auto-adding companion files to a selection — advisory only, ever.
- Capability inference from model configs or content. The tool asks
  "does the tree ship a known companion pattern?", never "is this a
  vision model?". No LLM, no heuristics beyond the curated table.
- Machine-readable plan output (`--json`) — a later spec if scripted
  use demands it.
- Fetching or verifying the cross-repo base model — the advisory
  names the follow-up pull; running it is the human's call.
- Recursive dependency resolution (base model of a base model).
- A standalone `plan` subcommand — this is a flag on `pull`, keeping
  one code path.

## Notes

- **Prerequisite housekeeping:** `pull.py` (291 lines) is at the
  300-line cap and this feature touches it; the standing TODO item
  says split before the next feature lands. The split rides ahead of
  or with this branch.
- Most `--plan` machinery already exists: `plan_downloads()`,
  `total_selected_size()`, `require_disk_space()`, and
  `resolve_model_id()` already compute everything the dry run prints,
  before any bytes move (`pull.py:215-267`). The feature is largely
  "stop at the confirmation point, print, exit."
- The pattern table's entries assert facts about ecosystem naming
  conventions (llama.cpp `mmproj` naming, PEFT `adapter_config.json`
  schema). Per the external-reference provenance rule, the implement
  phase must pin a fetched source URL + retrieval date for each row
  in `## External references` here before the values land in code.
- Resolved (2026-07-12): advisories evaluate on every pull mode.
  Same-repo rows can't trigger under `--whole-repo` (everything is
  selected); cross-repo rows (adapter base, quant master) fire in all
  modes.
- Scoped out to a future spec (0006 candidate): a guided discovery
  workflow — start from a model name, list the hub model tree's quant
  children, pick a repo, then select interactively. Feasible with
  deterministic hub metadata (no LLM), but it revisits the
  exact-repo-ids-only stance, which is a product-level call for 0000
  first.
- Resolved (2026-07-12): all advisory rows are archive-aware — a
  companion already archived from an earlier pull produces no
  advisory. An advisory means "you are missing this", never "this
  exists".
- Review adjudications (2026-07-12, one round of /review +
  /review-adversarial + /security fully resolved):
  - Runnable `run: llm-preserver pull ...` remedies are emitted only
    for well-formed hub repo ids; hostile metadata gets a non-command
    advisory. `--include` remedies are shell-quoted.
  - The plan report itself discloses the adapter-config fetch and its
    closing line reads "no weights downloaded" when one happened.
  - Adopt-only pulls (reconcile-by-hash, zero bytes moving) skip the
    size confirmation.
  - Accepted trade-off: archive-awareness matches companion basenames
    across all of a model's artifacts, so two grouped quant repos
    shipping same-named projectors share one advisory silence —
    selecting the second copy would hit the reconcile hard stop
    anyway.
  - All hub-derived text (report lines, advisory messages) passes
    through the `clean_text` sanitizer before reaching a terminal;
    the disk verdict and refusal derive from one disk read.

## External references

All fetched in-session 2026-07-12. Primary sources are MIT
(ggml-org/llama.cpp) or Apache-2.0 (huggingface/peft, transformers,
hub-docs) — no copyleft sources.

- **`mmproj-*` = multimodal/vision projector.** llama.cpp
  `convert_hf_to_gguf.py` (`--mmproj`: "An 'mmproj-' prefix will be
  added to the output file name"), `docs/multimodal.md`, and
  `common/download.cpp` (`gguf_filename_is_model` excludes `mmproj`).
  <https://github.com/ggml-org/llama.cpp> — MIT.
- **`mtp-*` = multi-token-prediction (speculative-decoding) head.**
  llama.cpp `common/download.cpp` hard-codes the prefix
  (`find_best_sibling(files, model, "mtp-")`; `--mtp` flag help).
  Recent convention (PR #22673, merged 2026-05-16); the head may
  also be embedded in the main GGUF — the sidecar is optional.
  <https://github.com/ggml-org/llama.cpp> — MIT.
- **`*imatrix*` = importance-matrix quantization calibration data.**
  llama.cpp `tools/imatrix/README.md` ("computes an importance
  matrix ... used during quantization"); `download.cpp` treats
  `imatrix`-named GGUFs as non-model companions.
  <https://github.com/ggml-org/llama.cpp> — MIT.
- **Shard naming.** HF transformers big-model sharding
  (`model-00001-of-00006.safetensors`,
  <https://huggingface.co/docs/transformers/main/en/big_models> —
  Apache-2.0) and llama.cpp `src/llama.cpp`
  `SPLIT_PATH_FORMAT = "%s-%05d-of-%05d.gguf"` — MIT.
- **`adapter_config.json` → `base_model_name_or_path`.** peft
  `src/peft/config.py` (field help "The name of the base model to
  use"; `save_pretrained` writes `adapter_config.json`).
  <https://github.com/huggingface/peft> — Apache-2.0.
- **Hub `base_model` model-card metadata.** HF Hub docs, "Specifying
  a base model": fine-tunes/adapters/quantizations declare
  `base_model` in card metadata; machine-readable via
  `huggingface_hub`.
  <https://huggingface.co/docs/hub/model-cards> — Apache-2.0
  (hub-docs).
