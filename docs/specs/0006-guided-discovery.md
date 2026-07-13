# 0006 — Guided Discovery

**Status:** shipped
**Last updated:** 2026-07-13

## Goal

The deterministic path from "I only know the model's name" to a
completed, correctly-grouped pull without leaving the tool. Today the
tool helps *within* a repo (interactive listing, advisories,
`--plan`) but demands an exact repo id to start — so the step before
(find the right repo among quantizers and derivatives) happens in a
browser. A new `discover` command closes that gap:

```
llm-preserver discover 'qwen coder' [ARCHIVE_PATH]
```

1. **Search** — the hub's own free-text search results, passed
   through verbatim (their ordering, never ours), as a numbered list
   with hub facts per row.
2. **Tree** — for the picked repo, the typed model-tree from
   `base_model` metadata, one hop each way: the parent chain upward
   (a user landing on a finetune or quant sees what it derives from)
   and derivative children downward, grouped by relation type
   (quantized / finetune / adapter / merge), each row carrying hub
   facts. Navigation is by numbered pick: descend into a child,
   ascend to a parent, or select the current repo to pull.
3. **Pull** — selecting a repo drops into the *existing* pull flow
   (file listing, advisories, size confirmation, staging, record),
   with the canonical model directory derived from the tree the user
   just navigated — no `--model` typed by hand.

Product foundation: the revised 0000 stance (2026-07-13) —
deterministic discovery is in scope; no LLM, no tool judgment. The
tool lists facts (downloads, publisher, last-updated, total size,
gated marker, relation type); the human picks; every pull still
targets an exact repo id. Primary user is Brian, interactively;
public users benefit but discovery may assume the tool's own
conventions.

Per the scope check (2026-07-13), the kill condition is the browser
test: if discovery's listing still sends the user to the hub website
to double-check — or choosing requires an AI agent driving the tool —
the feature failed its thesis and should be removed rather than
patched around.

## Success criteria

- Starting from `llm-preserver discover 'qwen coder'`, every step to
  a completed pull is a numbered pick inside the tool, and the
  resulting archive record is grouped under the correct canonical
  model with no `--model` flag typed by hand.
- Search results are the hub's, verbatim: same items, same order as
  the hub API returns for that query. The tool adds facts as columns,
  never re-ranks. An empty result set says so and exits cleanly
  (exit 0 — nothing failed).
- Both listing stages paginate from day one (adjudicated
  2026-07-13): each shows one page with a "showing X of Y" count and
  a `more` pick that fetches the hub's next page. When the API
  exposes no total, the footer falls back to "showing 1–N — more
  available"; per-row size appears only where the list response
  carries it (no per-repo calls for a column). Popular bases have
  hundreds of derivatives — a capped first page with no way forward
  fails the browser test outright. Ordering is always a *stated hub
  fact*, deterministically requested from the API — the hub's
  relevance order for search, hub-sorted-by-downloads (grouped by
  relation type) for tree children — never a score the tool
  computes.
- The tree stage shows, for the picked repo: its parent chain upward
  (repeated `base_model` hops until a repo declares none) and its
  direct derivative children downward, typed by the hub's relation
  metadata. Each row shows at minimum: repo id, relation type,
  downloads, last-updated, and a gated marker. Gated repos are
  listed and marked, never hidden; pulling one still requires the
  user's own `hf auth login`.
- A repo whose `base_model` points at a missing/renamed repo (stale
  metadata) is presented honestly — the declared id shown with a
  "not found on the hub" note — never silently dropped and never
  auto-followed to a guessed successor.
- Grouping is pull's job, not discovery's (review adjudication
  2026-07-13, superseding the earlier derived-`model=` shape): the
  handoff passes `model=None`, so the existing confirm-gated,
  format-directed default (spec 0004 rules) proposes the canonical
  home — the declared base for a GGUF/MLX conversion, the repo's own
  id for a derived model or an original — and the human answers y/n.
  Rationale: the derived-id shape was relation-blind (a finetune
  reached via discover grouped under its parent while `pull` groups
  it under its own id), let hub-authored metadata name an archive
  directory with no confirmation (security finding), and fired the
  0005 mismatch warning with false `--model` wording in the
  renamed-parent case. With no override, the mismatch warning is
  structurally silent, and "exactly as if the user had typed the
  repo id" holds literally. The success bar's "no `--model` flag
  typed by hand" stands — the confirmation is a y/n, not typing.
- The handoff lands in the unmodified pull flow: interactive file
  listing, advisories, size confirmation, and the record/manifest
  behavior of specs 0003/0004/0005 apply exactly as if the user had
  typed the exact repo id themselves. `discover ... --plan` composes:
  the flag turns the final pull into the 0005 dry run — same handoff
  seam, verify-then-run preserved (adjudicated 2026-07-13).
- Determinism invariant, testable: given the same fake hub responses,
  two discovery sessions with the same picks produce byte-identical
  listings and the same pull. No call to anything but the hub API;
  no scoring, no reordering, no LLM.
- Fault domains hold: failures during search or tree stages map
  through the existing four-domain table exactly as pull does
  (local-network faults exit 3, hub-side faults exit 4); a nonsense
  query is not an error (empty results, exit 0). (Adjudicated
  2026-07-13: earlier "exit 4" wording was shorthand for the
  existing framing.)
- `docs/cli.md` documents `discover` (stages, facts shown, the
  no-ranking invariant, gated behavior); README quick start gains the
  one-liner.

## Non-goals

- Any recommendation, ranking, scoring, or auto-pick by the tool —
  including a publisher-reputation table. Download counts and dates
  are the hub's facts and carry that signal; judgment stays human
  (0000, revised 2026-07-13).
- The quant-label annotation table (bits/weight, quality tier,
  common-default marker) — deliberately deferred to its own
  follow-up; it slots into the file-listing stage later without
  changing discovery's shape.
- Recursive tree crawling beyond one hop each way per navigation
  step (the user can keep hopping; the tool never walks ahead).
- A local search index or offline caching of hub metadata — every
  listing is live hub data.
- Non-interactive / scripted discovery. Discovery is a human picking;
  scripts already have exact ids, `--include`, `--yes`, and `--plan`.
- Sources beyond Hugging Face.
- Changes to gated-repo handling: listings mark gated status; auth
  remains `hf auth login`, no tool flags.
- No LLM anywhere in the flow — if choosing requires wrapping the
  tool in an AI agent, the feature has failed (kill condition), not
  grown a requirement.

## Notes

- **New CLI surface:** `discover` is the second network-touching
  command. It reuses the `hub.py` seam; whatever hub-API calls the
  search and tree stages need (hub search endpoint, model-tree /
  `base_model` child listing via `huggingface_hub`) must land behind
  `HubClientProtocol` so the fake-hub test seam covers discovery
  end to end.
- **External references to pin at implement time:** the
  `huggingface_hub` APIs used for free-text search and for listing a
  model's typed derivatives (the model-tree relation metadata), per
  the provenance rule — source URL, retrieval date, license into
  `## External references` before the calls land in code.
- Stale-`base_model` reality (live-verified 2026-07-12: a quant
  repo's declared base was a pre-rename id the hub now redirects):
  discovery inherits this noise and must present it, not paper over
  it — see the success criterion.
- Live finding (manual verification 2026-07-13): the hub's
  model-tree index (the `baseModels` expand discovery reads) is
  *already rename-resolved* by the hub — the same repo whose card
  declares a stale pre-rename base lists its current parent id in
  the tree. So the "renamed" link marker rarely fires in practice;
  it and "not found on the hub" chiefly guard deleted parents and
  between-call drift. The card's `base_model` (what pull's
  full-precision-master advisory reads) remains stale-able — two
  sources, different freshness.
- Relation to the live-hub canary (0000 roadmap): discovery widens
  the surface that server-side metadata drift can break; the canary
  is the early-warning mechanism, not this spec's scope.
- Review adjudications (2026-07-13, one round of /review +
  /review-adversarial + /security fully resolved):
  - Grouping via `model=None` (see the success criterion above) —
    the load-bearing one.
  - Tree `m` advances every non-exhausted relation pager one page,
    so a base with hundreds of quants cannot make its finetunes
    unreachable.
  - An endless invalid-input stream is refused after 5 consecutive
    misses (exit 2, naming interactivity) instead of livelooping;
    EOF quits cleanly (exit 0).
  - Hub-metadata hardening: `base_model` values are validated
    against the repo-id grammar before becoming request parameters;
    parent chains cap at 10 hops; malformed `baseModels` shapes
    yield None instead of unmapped crashes; every hub-derived echo
    passes `clean_text`.
  - Accepted exposure: no in-session caching — re-entering a tree
    refetches its listings, and one hub failure mid-navigation ends
    the session (exit 4, navigation state lost). Within the
    no-caching non-goal; the live-hub canary is the drift watchdog.
  - Rename resolution in the pull path (adjudicated 2026-07-13,
    also recorded in spec 0005): live use showed the discover tree
    naming a parent by its current id while the pull's grouping
    proposal and master advisory named the card's stale pre-rename
    id — two sources on one screen. Every pull now spends one light
    metadata call resolving the declared base to its current hub id
    (disclosed via an INFO line), so grouping proposals, the
    mismatch warning, the master advisory, and archive records all
    carry names that still resolve. The second sanctioned exception
    to the one-metadata-call rule (the adapter-config fetch is the
    first); unresolvable or malformed declared bases fall back to
    the declared name and never abort a pull.
- Sizing: Medium (new CLI command + hub client extension + interactive
  flow + docs); full loop applies. `records.py` is untouched here —
  its near-cap split rides whichever spec next touches it.

## External references

Verified in-session 2026-07-13 against the live hub API and the
installed `huggingface_hub` 1.23.0 (Apache-2.0). Live verification
was preferred over docs prose — every value below was confirmed by a
real API round-trip:

- **Free-text search**: `HfApi.list_models(search=..., limit=...,
  expand=[...])`; results in the hub's relevance order when no
  `sort` is passed.
  <https://huggingface.co/docs/huggingface_hub/package_reference/hf_api>
- **Model-tree children**: `list_models(filter=
  "base_model:<relation>:<repo_id>", sort="downloads")` — verified
  live to return exactly the typed children (the `other=` query
  variant is ignored by the API; do not use it). Relations:
  quantized / finetune / adapter / merge.
  <https://huggingface.co/docs/hub/model-cards> ("Specifying a base
  model", hub-docs Apache-2.0).
- **List-response fields**: `expand=["downloads", "lastModified",
  "gated", "baseModels"]` (verified valid-expand list from the API's
  own error response); `gated` is `False | "auto" | "manual"`;
  `baseModels` lands on `ModelInfo.base_models` as
  `{"relation": ..., "models": [{"id": ...}]}`. No per-row size
  field exists in list responses; no total count is exposed (cursor
  pagination via `Link: rel="next"`).
