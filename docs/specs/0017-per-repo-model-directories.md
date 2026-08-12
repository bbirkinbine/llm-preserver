# 0017 — Per-Repo Model Directories

**Status:** shipped
**Last updated:** 2026-08-11

## Goal

Make a model directory mean exactly one thing: **the bytes of one
Hugging Face repo**. Today the directory is named for the *original*
model and third-party quants file underneath it, so where a pull lands
is decided at pull time by a mix of someone else's model card and a
prompt answer the archive never records. Measured on the live archive
2026-08-11: two GGUF repos of the same model sit in two different
shapes despite declaring the same base, with nothing on disk to explain
the difference, and three directories are named for models whose
weights the archive does not hold at all — `status` reports them as
archived. This spec flattens the
layout to one directory per source repo, moves lineage from the
filesystem into the record where `status` / `show` /
`MODEL-RECORD.md` can state it, and ships the `migrate` command that
converts an existing archive — in place, or as a copy at a new root —
without re-downloading or re-hashing a byte. Design is governed by
[ADR 0003](../adr/0003-one-directory-per-source-repo.md); this spec
implements it and does not re-argue it.

The migration is the load-bearing half. The layout change is a day's
work on a fresh archive; the value of this tool is the shelf that
already exists, and a conversion path that leaves it verifiably intact
is what makes the decision safe to accept.

## Success criteria

Behavior-level, in terms of the live archive's own shapes.

**Layout and pull**

1. `pull unsloth/Muse-Glimmer-30B-GGUF --include '*Q4_K_M*'` lands
   files under `models/unsloth/Muse-Glimmer-30B-GGUF/gguf/` and asks
   **no** grouping question, whatever the repo declares as
   `base_model`. The same holds for a repo that declares one
   (`meta-models/Muse-Glimmer-30B-GGUF` → its own directory).
2. `pull --model CREATOR/MODEL` is removed. Passing it exits 2 with a
   message naming the replacement (pull the repo id you want
   archived).
   On `verify` the same flag is **renamed `--repo`, with `--model`
   kept as an accepted alias** (adjudicated 2026-08-11) so the CLI
   vocabulary matches the ADR — a directory is a repo, its first
   component an owner — without breaking anything already scripted.
   The alias prints a one-line note naming `--repo`; `--help` documents
   only `--repo`. `--repo` on `migrate --to` (criterion 11) uses the
   same word from the start.
   **`remove` is excluded from the rename** (corrected 2026-08-11 after
   `/test-first` found the flag does not exist): its model id is a
   *positional* argument, and making it optional so `--repo` could
   replace it recreates the two-optional-positionals shape spec 0009
   found unparseable against the env-var archive path. `remove` keeps
   its positional and changes vocabulary only — help text and docstring
   move from `<creator>/<model>` to `<owner>/<repo>`.
3. For every model directory in a migrated archive, all three agree:
   the path, the record's `hub_id`, and every artifact's
   `source_repo`. `verify` reports a mismatch as **`unmigrated`** —
   a verdict on its own axis, **appended to** the fixity word rather
   than replacing it (`valid, unmigrated`), so spec 0009's
   complete-vs-valid vocabulary survives and a `--quick` run stays
   distinguishable from a completed hash run (adjudicated 2026-08-11).
   It **exits 1**, joining the
   other "your archive has a problem" outcomes so a scheduled verify
   goes red until the layout is converted. The line names the
   offending source repo and the `migrate` command.
   A ``source_repo`` that is *present but unparseable* also convicts
   (adjudicated 2026-08-11): the record asserts an origin, and one the
   tool cannot read is one it cannot confirm is the directory's own —
   reporting that as `ok` would let a misfiled directory verify clean.
   A *missing* claim still carries no contradiction and is skipped.
   `verify` judges **per directory**, so a half-converted archive
   reports each one honestly; mixed state is expected, not an error,
   because criterion 13 makes migration resumable and 683 GiB of
   renames is not instantaneous.
   **Precedence when a model is both unmigrated and drifted: drift
   wins** (adjudicated 2026-08-11) — exit 5, spec 0009's meaning, with
   `unmigrated` still printed on the line. Exit 1 is reached only when
   nothing drifted. A misfiled directory is a layout problem; a broken
   hash is damage, and damage outranks tidiness.
4. A pull needs no hub metadata call to decide its destination; the
   destination is a pure function of the typed repo id.

**Lineage after flattening**

5. The record carries `base_model` (declared lineage, nullable). A
   pull records what the card declares; it never invents one.
6. **`pull --base-model <repo-id>` records curator-asserted lineage**
   for a repo whose card declares none, or declares one that is stale
   or wrong. It affects the record only and never the path. The record
   distinguishes asserted from card-declared, so provenance stays
   honest about which claim a human made. Rationale: `--model` is
   being deleted, and it was doing *two* jobs — choosing the directory
   and asserting "this is a conversion of that." Only the first job
   should disappear. Precedent for the shape is the existing `--role`
   flag (`cli/pull_cmd.py:41`), which is likewise curator judgment
   recorded at pull time.
7. **Lineage surfaces shelf-wide, not just per model** (adjudicated
   2026-08-11). `status` **groups by lineage**: a derivative indents
   under the base it declares, and a base the archive does *not* hold
   is printed as a parenthesised `not archived` header above its
   derivatives — which is where the 3 pure-rename directories land, so
   the shelf states plainly that it holds unsloth's conversion of
   Qwen3-Coder-Next and not the model itself. This deliberately
   replaces what the old nested layout gave for free; its cost is that
   `status` output gains leading whitespace, so anything parsing it
   by column sees a changed shape (called out in the docs sweep).
   `show <repo-id>` prints the same relationship for one model, both
   directions it is known — what this repo declares as its base, and
   which archived repos declare *this* one as theirs.
8. `MODEL-RECORD.md` states the same relationship in prose, so the
   `ls`-and-`cat` reader with no tool still learns that a quant
   directory derives from a model directory (ADR 0001's durability
   test, which the old layout satisfied structurally).

**Migration**

9. `migrate --plan` on the live archive prints, changing nothing: every
   directory to be renamed, every artifact to be moved out, the target
   path for each, byte totals, and any collision that would block the
   run. On today's archive that is 3 pure renames (138.0 GiB) and 8
   splits (544.5 GiB of foreign payload).
10. `migrate` (in place) converts the archive by moving **exactly the
    files each foreign artifact lists** — not whole format
    subdirectories (adjudicated 2026-08-11). `update_record` keys
    artifacts by `(format, source_repo)`, so one `gguf/` directory can
    legally hold two publishers' files from selective pulls, and
    `require_single_snapshot_source` only ever guarded `--whole-repo`.
    Moving the subtree would relocate a second repo's bytes. Paths stay
    model-dir-relative and unchanged, so hashes survive either way, and
    emptied subdirectories are `rmdir`-ed. Records are rewritten **No payload byte is re-downloaded, re-hashed, or
    rewritten** — recorded hashes stay valid because file paths *within*
    an artifact do not change, and `manifest-sha256.txt` is regenerated
    from the record.
11. `migrate --to <new-root>` writes a converted archive at a new root
    and leaves the source archive untouched and still usable.
    **`--to` accepts `--repo <repo-id>` (repeatable) to copy only the
    named repos** (adjudicated 2026-08-11), so a rehearsal costs
    minutes rather than a whole-archive transfer. The result is a valid
    schema-2 archive holding a subset — a partial copy is a supported
    state under ADR 0001, not a broken one. Byte copy only: hardlinking
    payload was considered and rejected, because two trees sharing
    inodes cuts against ADR 0001's immutable-payload separation and
    network-share support for it is unverified here.
12. A cross-filesystem move surfaces as a clean error naming `--to`,
    never a silent copy-and-delete. (The live archive is a single
    filesystem, so an in-place `mv` must be a rename or it must say so.)
13. Migration is **idempotent and resumable**: re-running it on a
    half-migrated archive re-plans from disk state and completes, and
    re-running it on a migrated archive is a no-op reporting nothing to
    do. There is no journal file to lose — the plan is derivable from
    `hub_id` vs. `source_repo` vs. path.
14. Migration **harvests the lineage the old layout asserted**: when
    `unsloth/X-GGUF` files move out of `Qwen/X`, the new record's
    `base_model` is set to `Qwen/X`. That relationship was a curator's
    confirmed judgment at pull time and exists nowhere else once the
    path is gone.
15. After `migrate`, `verify` reports every model `valid` with no
    re-download, and `status` lists the same total bytes it listed
    before.
16. Non-interactive `migrate` without `--yes` refuses (exit 2) rather
    than acting on a piped answer, matching `remove` (spec 0010).
    Ctrl-C reprints the re-run command.
17. **An owner directory left empty by a split is removed with
    `os.rmdir`, never `rmtree`** (adjudicated 2026-08-11) — the call
    fails rather than recursing if anything at all remains, including a
    dotfile, so a wrong plan cannot destroy content. Each removal is
    named in the preview before the confirm. This is the only deletion
    `migrate` performs, and it deletes no file, ever: payload leaves a
    directory by being moved out of it, never by being unlinked.
    Declared here because `CLAUDE.md`'s don't-touch list reserves
    deletion inside an archive to `remove` (spec 0010); this is a
    narrow, spec-sanctioned second case.

**Runtime views survive the move**

18. **Re-running `views <same-dest>` after migration produces a working
    tree**, with every symlink pointing at the model's new path and
    every stale one pruned — proven the way spec 0002 phase 1 was
    proven, by loading and serving an archived model through the
    regenerated store, not by inspecting links. The pruning path
    (`views/ollama.py:326`) currently keeps only links that resolve
    *into* the archive; post-migration links are dangling, so whether
    they still satisfy that containment check is a test to write, not
    an assumption to make.
19. `migrate` ends by printing the exact `views` command to re-run for
    each destination passed as **`--view-dest <path>` (repeatable,
    adjudicated 2026-08-11)**. The path is composed into printed text
    and **never opened** — migrate does not touch a view tree, it only
    tells you what to run. An unconditional line says that view trees
    it was not told about are stale until refreshed, since no
    archive-side registry exists.

**The CLI works end to end afterwards**

20. Every command runs correctly against a migrated archive before this
    spec is done: `init`, `status`, `show`, `pull` (new repo, re-pull
    no-op, `--whole-repo`, `--include`, `--plan`), `discover`
    (search → tree → pull) and `discover --match-ollama`, `verify`
    (plain, `--quick`, `--repo` and the `--model` alias, `--staging`),
    `remove` (positional id, whole and
    `--include`), `views`, and `migrate` itself re-run as a no-op.
    Half of these need no code change (see `## CLI impact`) — they
    still get exercised, because "no edit needed" is a claim the gate
    cannot see.
21. The live check runs on a **scoped copy** of the real archive —
    `migrate --to <root> --repo <repo-id>` on one pure rename
    (`zai-org/GLM-4.7-Flash`, 16.4 GiB) and one split
    (`google/gemma-4-31B-it`, 83 GiB) — never the only copy. The real
    conversion then runs in place. Results recorded in this spec before
    the status flips to shipped.

**Versioning**

22. Archive `schema_version` 1 → 2; record `record_schema_version`
    2 → 3.
23. **A v1 archive must convert before it accepts new content**
    (adjudicated 2026-08-11). `pull` and `remove` refuse with exit 2,
    naming `migrate` as the fix and how many directories are affected.
    `status`, `show`, `verify`, and `views` keep working, so the
    archive stays inspectable and runnable throughout. The gate is
    scoped to commands that **add or relocate model content**, not to
    writes in general — `verify` refreshes `manifest-sha256.txt`
    (`verify.py:246`) and must not block itself.
24. **The archive marker flips 1 → 2 only after a full successful
    migration**, so a half-converted archive is still v1 and the
    content gate stays closed for the whole conversion window. That
    also makes the flip the single durable signal that migration
    finished, consistent with criterion 13 deriving everything else
    from disk state.
25. Refusing `pull` on an unmigrated archive is what keeps the
    duplicate case at zero: a pull writing the new layout into an
    unconverted archive would create `unsloth/X-GGUF/` while the same
    repo's files still sit in `Qwen/X/gguf/`, turning every later
    migration into a merge. Measured 2026-08-11: the live archive has
    **no** such collisions today, and this gate is why it stays that
    way.

## Non-goals

- **No re-download and no payload re-hash during migration.** Fixity
  proof is `verify`'s job, run afterwards on the human's schedule.
- **No dedup** of identical bytes across two publishers' repos.
- **No *tool-invented* lineage claims.** Three sources are allowed and
  each is attributed in the record: the card declares it, the human
  asserts it with `--base-model`, or migration harvests it from an old
  nested path. The tool never guesses a base for a repo that named
  none — the standing no-tool-judgment stance (`docs/specs/0000-product.md`).
- **Not spec 0016's artifact classification.** This spec takes only the
  lineage field (`base_model`), which the layout change requires
  because it is where lineage now lives. The
  `adapter | quant | full-weights | unknown` taxonomy stays 0016's
  problem and is unaffected either way. The `record_schema_version` 3
  collision with 0016 is settled in this spec's favour (adjudicated
  2026-08-11): **0017 takes 3**, and 0016 re-bases to 4 — noted in that
  spec so the next session does not rediscover it.
- **No `status` redesign beyond the lineage grouping** criterion 7
  settles. Roles, sizes, and completeness keep rendering as they do;
  a `base` column *instead of* grouping was considered and not taken.
- **No archive-side registry of view destinations.** The view marker
  lives in the *destination* and names the archive
  (`views/dest.py`), not the other way round, so migration cannot
  enumerate the view trees that exist. Repair is a re-run the human
  aims at a dest they know (criterion 18); teaching the archive to
  track its own views is a separate decision, listed as an open
  question.
- **No rollback command.** `--to` is the reversible mode; in-place is
  the mode you choose when you accept it is one-way.

## External references

**None — original / empirical.** No outside authority, registry, or
specification is claimed by this spec.

Every number in it comes from measuring the live archive on
**2026-08-11** with read-only walks of `model-record.json` (25 models;
11 of 25 directories holding a foreign `source_repo`; 3 pure
renames at 138.0 GiB; 8 splits at 544.5 GiB). Those figures describe
one archive at one moment — they justify the design and size the work,
and no implementation value may be derived from them. The Hugging Face
repo-id shape the layout mirrors is already implemented and validated
in `records.ID_COMPONENT_RE`; this spec adds no new claim about hub
conventions and requires no `WebFetch`.

## Sketch

**Path derivation.** `models/<owner>/<repo>` from the typed repo id,
both components already validated by `ID_COMPONENT_RE`. Delete
`propose_default_home` / `confirm_default_home` / `parse_model_id` /
`require_single_snapshot_source` and the `--model` plumbing through
`pull_prepare` / `pull_exec`; spec 0006's rename-resolve hub call goes
with them, and `discover`'s pull hand-off simplifies to passing the
repo id it already has.

**Record.** Add `base_model: str | None` plus the source of that claim
(card-declared, curator-asserted via `--base-model`, or harvested from
an old nested path during migration) — three ways a lineage line can
arrive, and a record that flattens them into one field cannot be
audited later. Bump `RECORD_SCHEMA_VERSION` to 3 and
`archive.SCHEMA_VERSION` to 2. The existing `_PreservingModel` behavior
and the "add rather than rename" conservatism (ADR 0001) apply.

**`--base-model` on pull** follows `--role`: parsed and validated like
any repo id, recorded, never consulted for the destination path.

**`migrate` command.** Plan is derived, not stored:

1. Walk `models/*/*/model-record.json`. For each artifact whose
   `source_repo` differs from the record's own `hub_id`, emit a move:
   `(source dir, format subtree, target dir from source_repo)`.
2. A directory whose artifacts are *all* foreign and share one
   `source_repo` is a whole-directory rename, not a split.
3. Refuse the whole run on any collision (target exists with
   conflicting contents), unreadable record, or path that resolves
   outside the archive root — the `resolve()` / `is_relative_to`
   posture specs 0009 and 0010 established, since this walk follows
   recorded paths.
4. Preview, then confirm (or `--yes`; non-interactive without it
   refuses).
5. Execute per unit, ordered so an interruption leaves a state the next
   run re-plans correctly: move the artifact's listed files, write the target
   record, rewrite the source record without the moved artifact,
   regenerate both manifests, then `rmdir` the source model directory
   and its owner directory if each is empty (never `rmtree`).
   Record-last preserves the existing convention — a crash leaves
   payload the next plan still sees.
6. `--to <root>` copies instead of moving, into a fresh archive marked
   at schema 2, leaving the source untouched. With `--repo` the walk
   is filtered to the named source directories before planning, so the
   copy carries only those models and whatever they split into.

**Landing order.** This is Large work touching well past five files, so
it runs as checkpointed passes on this branch rather than one change.
Migration lands before the pull simplification on purpose: the escape
hatch must exist before anything can strand an archive.

**No pass is a merge unit.** The six land as one PR. Mid-branch states
are deliberately inconsistent — pass 1 teaches `verify` a verdict whose
remedy command does not exist until pass 2 — and merging any of them
alone would ship an alarm without its fix.

1. Record schema bump + `base_model` field + the three-way invariant
   and its `unmigrated` verdict in `verify`. Read paths tolerate both
   layouts. **Done** — the archive-marker bump moved to pass 3.
2. `migrate` (plan / in-place / `--to`), against archives still in the
   old layout — the escape hatch exists before anything else moves.
3. `pull` simplification: grouping and `--model` deleted, and the
   archive `schema_version` 1 → 2 bump lands here with them. **The
   `pull --model` refusal (criterion 2) lands here, not in pass 1**
   (corrected 2026-08-11 during implementation): the flag stays
   functional while `pull_grouping` still proposes a home, and 57 call
   sites across 21 test files use it to pre-answer the grouping prompt.
   Refusing it earlier would rewrite those tests twice — once to answer
   a prompt that is about to disappear, again when it does.
   `tests/test_cli_pull_model_removed.py` holds the two tests, skipped
   with that reason; drop the marker in this pass.
4. `pull`: `--base-model` added.
5. Lineage rendering in `status` (grouping), `show`, and
   `MODEL-RECORD.md`, view repair
   (criteria 18-19), and the full-CLI pass (criterion 20).
6. Docs sweep — `docs/data-structures.md` (the layout tree and both
   identity rules), `docs/cli.md` (`pull --model`'s removal, the
   `--repo` rename and its `--model` alias, the grouping paragraph, the
   goal table, and the new `status` output shape), `README.md`,
   `docs/adr/0001-model-storage.md` forward pointer, and a grep for
   `canonical` across `docs/` and `src/`.

**Test strategy.** `tmp_path` archives only, never a real archive
(`CLAUDE.md` don't-touch list). Fixtures reproduce both live shapes: a
pure-rename directory (all artifacts foreign, one source) and a split
directory (own snapshot plus a foreign quant). Assert paths, records,
manifests, and a clean `verify` after migration; assert idempotent
re-run; assert a mid-run interruption (kill between subtree move and
record write) re-plans and completes; assert cross-filesystem move
raises rather than copies; assert collision and symlink-escape refusals.
Build a view against an old-layout fixture, migrate, re-run `views`, and
assert every link resolves to the new path with no dangling leftovers —
the criterion-18 question the current pruning containment check may not
answer. Mutation-test each guard by deleting it and confirming a test
goes red.

## CLI impact

Surveyed against the code on 2026-08-11. The load-bearing fact: a model
id stays **two components** and every path build stays
`models/<a>/<b>` — only the *meaning* of the components changes (repo
owner/repo, not creator/model) and where the id comes from (typed, not
inferred). So the layout change is concentrated in `pull` and is mostly
deletion.

**Path plumbing is indifferent everywhere.** No command's
directory-building code changes; what changes is output and flag
names, decided by the clarification round rather than forced by the
layout.

| Command | Path code | What this spec adds |
| --- | --- | --- |
| `init` | No model paths | Marker at schema 2 (criterion 22) |
| `status` | `inventory()` walks `models/*/*` | Lineage grouping in the renderer (criterion 7); 25 → ~36 rows |
| `show <id>` | `cli/archive_cmds.py:118`, two-component id | Lineage both directions (criterion 7) |
| `verify` | Same walk; `.staging/<a>/<b>` mirrors it (`pull_prepare.py:182`) | `unmigrated` verdict at exit 1 (criterion 3); `--repo` rename (criterion 2) |
| `remove` | `remove/plan.py:79`; guards check `base / creator / name` (`remove/models.py:100`) | Vocabulary only (its id is positional, not a flag); refuses on a v1 archive (criterion 23) |
| `views` | Record-driven (`views/sources.py:49`); `_mint_name` splits on `/` (`views/ollama.py:209`) | Must survive a re-run after migration (criterion 18) |

**`discover` — works, and loses a prompt.** Search, tree navigation,
paging windows (0015), and the archive-mode choice are hub-side with no
archive-layout dependency. The pull hand-off
(`cli/discover_cmd/flow.py:61`) already passes `model=None` with a
comment recording spec 0006's adjudication that *hub metadata must
never name an archive directory without a human yes*. Under this ADR
that invariant holds structurally — the human pressed `0` on a specific
repo row and that repo id **is** the directory — so the concern and the
comment both retire. The discover→pull path becomes: search → navigate
→ pick-files-or-whole-repo → confirm files → download, with the
grouping y/N gone.

`discover --match-ollama` needs nothing: its footer already composes
`pull <repo-id> --include <file>` with no `--model`
(`compose_pull_command`, `cli/discover_match.py`). Today that pasted
command lands wherever grouping decides; after this change it lands
where the printed repo id says, which makes the footer honest.

**`pull` — the only surgery, and it is subtractive:**

- `--model` deleted (`cli/pull_cmd.py:37`) along with
  `pull_grouping.propose_default_home` / `confirm_default_home`.
- `require_single_snapshot_source` deleted — two source repos can no
  longer share a tree, so the hazard it guards cannot arise.
- The "explicit `--model` disagrees with the declared base" advisory row
  goes with it (`pull_advisory.py:164-183`).
- `--plan`'s `would ask:` lines lose the grouping prompt
  (`pull_report.py:63`).
- Spec 0014's tentative-home dance collapses: the destination is known
  from the typed id before any hub call, so "resolve tentatively, plan,
  then ask" becomes "plan, then ask about files only." 0014's security
  fix — prompt-skip scoped to *user-chosen* homes because a hub-derived
  home could be steered — becomes moot, since every home is now
  user-chosen by construction.
- The resume hint drops `--model` (`cli/resume_hint.py:76`), so the
  printed command is exactly reproducible from the repo id — which was
  spec 0007's goal, reached by removing the thing that made it hard.
- Spec 0006's rename-resolve hub call (`pull_metadata.resolved_base_model`)
  stops being load-bearing for the destination but **is kept**
  (adjudicated 2026-08-11): criterion 5 records `base_model`, and a
  parent id that has since been renamed away ages badly in a
  preservation record. It stays a disclosed, advisory call — it can no
  longer name a directory, which was the only reason it was risky.

**Breaks and needs a human step:** existing `views` output. Ollama blob
symlinks point into old archive paths, so migration invalidates them.
Views are disposable by design (ADR 0001) and the fix is regeneration —
but `migrate` should *name* the view trees it invalidated rather than
let the human find out at model-load time.

## Open questions

Deliberately left for `/plan` and the human checkpoint.

1. **Should the archive track its own view destinations?** Today the
   marker points one way (dest → archive), so migration cannot find the
   trees it invalidated. Recording dests archive-side would let
   `migrate` refresh them, at the cost of new state that goes stale
   whenever a dest is deleted or moved. Out of scope here; worth its
   own decision.
2. **Does migration touch `.staging/`?** Leftovers there are keyed by
   the old layout (spec 0012). Detection is read-only today, so the
   cheap answer is to leave them and let `verify --staging` keep
   reporting them under whatever path they have.
3. **What does migration do with an artifact whose `source_repo` is
   `null`?** The field is nullable and the plan derives from comparing
   it to `hub_id`, so a null defeats the comparison. Measured
   2026-08-11: the live archive has **zero** such artifacts, so this is
   a correctness question for other archives, not a blocker here.
   Default: refuse the run and name the directory, rather than guess
   the artifact belongs where it sits.
4. **Collision handling: merge or refuse?** The sketch says refuse when
   a target already exists. For a target that is the *same repo*,
   merging is arguably right — it is what a re-pull produces. Measured
   2026-08-11: zero collisions on the live archive, and criterion 25's
   gate is designed to keep it that way, so refuse-and-report is the
   safe default until a real case appears.


## Implementation notes

Appended during implementation; the design above is unchanged except
where noted.

- **Two sequencing corrections, both the same lesson: the alarm must
  not precede the fix.** `pull --model`'s refusal moved from pass 1 to
  pass 3 — the flag stays functional while grouping still exists, and
  refusing earlier would have rewritten 57 call sites across 21 test
  files twice. The archive `schema_version` bump moved from pass 1 to
  pass 3 for the same reason at a deeper level: stamping a fresh
  archive v2 while `pull` still wrote v1 content manufactured exactly
  the state criteria 23-25 exist to prevent, and made the marker
  useless as the "migration finished" signal.
- **Criterion 2 was wrong about `remove`.** Its model id is a
  positional argument, not an option, so there was no flag to rename;
  making it optional would recreate the two-optional-positionals shape
  spec 0009 found unparseable. `remove` changed vocabulary only. Found
  by `/test-first`, corrected in the criterion.
- **Criterion 18 is closed on real hardware** (2026-08-11), not only in
  `tmp_path`. `views --seed-store` against the converted archive built
  56 blob links with **zero dangling**, every target resolving into the
  new `<owner>/<repo>` layout; Ollama 0.32.6 listed the models and
  `ollama run gpustack/bge-m3-gguf:q2_k` returned real embeddings —
  loaded and served through a symlink into the archive, no payload
  copied. The mechanism is what the tests predicted: blob names are
  content digests, so a stale link keeps its name and
  `_place_blob_link` re-points it on finding `readlink() != target`.
  `tests/test_views_after_migrate.py` pins it.
- **`migrate` moves files, never subtrees**, per the plan round's
  catch: one `gguf/` directory can legally hold two publishers' files
  from selective pulls.
- **Live use found the removal plan wrong before any conversion ran**
  (2026-08-11, Brian's `migrate --plan` on the real archive). The
  archive nests docs at `gguf/docs/<publisher>--<repo>/`, up to four
  levels on DeepSeek. The plan listed a parent *before* its own child
  and never listed the intermediate `gguf/docs` at all, so `os.rmdir`
  would have failed on the parent — silently, since the executor
  swallows it — leaving empty shells and breaking a promise the
  preview had made. Nothing was ever at risk (`rmdir` refuses a
  non-empty directory), but a plan that cannot keep its word is the
  exact failure `_emptied_dirs` was written to prevent. Fixed by
  deriving candidates from the *recorded* paths — every ancestor of
  every moving file, which is also what makes a resumed run see a
  directory an interrupted one already emptied — and ordering them
  deepest-first. Regression: `tests/test_migrate_nested_removals.py`.
  Worth noting the shape of the miss: 90 pass-2 tests and three
  reviewers did not catch it, because every fixture nested one level.
- **A directory with no record is skipped when planning**, not
  refused. That is precisely the state an interrupted run leaves at a
  target, and refusing it would make migration un-resumable.

### Live rehearsal on the real archive (2026-08-11)

Brian's `migrate --to` rehearsal on `zai-org/GLM-4.7-Flash` found two
defects that 90 pass-2 tests, three reviewers, and a synthetic
end-to-end run all missed. Both are now fixed and regression-tested.

1. **`EPERM` moving locked payload.** ADR 0001 locks payload after
   download (`chmod a-w`); over SMB that lock comes back as the BSD
   **user-immutable flag** (`uchg`) — the client stores the mode as the
   DOS read-only attribute and the macOS client surfaces it as
   `UF_IMMUTABLE` — and `rename(2)` on an immutable file fails with
   EPERM. Migration could not move a single archived file. `_move_file`
   now clears the flag for the length of the rename and restores it on
   the moved file, mirroring the unlock/relock `pull_transfer` already
   does for the write bit; a test pins that the lock is *borrowed, not
   spent*. Verified live: the converted copy's payload still carries
   `uchg`. Confirmed against the archive that metadata files carry no
   flags, so ADR 0001's "immutable payload, mutable metadata" split
   survives SMB intact — the first fixture wrongly locked everything.
2. **`--to` could not be retried.** After the EPERM, the destination
   held a complete 16 GiB copy and a partial conversion; re-running
   died with an unhandled `FileExistsError` from `shutil.copytree`
   (`dirs_exist_ok` unset) — the 0011/0012 traceback class again. The
   copy is now resumable and skips files already present at the right
   size. A rehearsal that cannot be re-run is not a rehearsal, and
   re-paying ten minutes to copy bytes already on disk is its own
   reason to skip them.

3. **`--base-model` was silently discarded on a nothing-to-do pull**
   — found by running criterion 6's own flag against the real archive.
   Spec 0014's no-op path returns before `update_record`, so the
   assertion was accepted at the command line, never written, and the
   command exited 0 saying "nothing new to pull". Silently ignoring an
   explicit instruction is the same class of fault 0014 itself closed.
   **`--role` had the identical hole and had had it since 0014
   shipped.** Metadata flags are record edits, not downloads, and
   already-archived is exactly when a curator wants to *correct* a
   lineage claim — so a no-op pull carrying either flag now writes the
   record and says so on its own final line (a third outcome, distinct
   from both "pulled" and "nothing new to pull"). Asserting the value
   the card already gave still counts as a change, because
   `base_model_source` exists to record *who* vouched for it; asserting
   an identical claim with identical attribution stays a true no-op.

**Also surfaced, and *not* fixed here: `remove` has the same EPERM
bug.** `remove/execute.py` unlinked payload with a plain
`Path.unlink()` and no flag handling, so on this archive it would fail
the same way — and because it deletes the record *first* by design, it
would have orphaned the payload. Pre-existing on `main`; fixed the same
day once `migrate` needed the helper, and both now share
`file_locks.py`.

**What the rehearsal measured.** Re-running after the fix completed in
**0.5 seconds** — bytes already on disk were skipped and the rename is
a metadata operation server-side. `verify` on the converted copy
re-hashed all 17.5 GB and returned `valid` at exit 0. That is the
answer to "will the in-place run take a while": 682.6 GiB of renames
is seconds, not hours.

### End-to-end verification (2026-08-11, throwaway archive)

Run against a hand-built v1-layout archive through the installed CLI,
not the test suite: `verify --quick` reported
`complete, unmigrated` at exit 1 naming the offending repo and the
remedy; `pull` refused at exit 2 naming `migrate` and the count;
`migrate --plan` previewed the rename and all three directory
removals; `migrate --yes` converted it; `verify` then reported `valid`
at exit 0 with no re-download and no re-hash; `status` rendered the
absent base as `(Qwen/tiny-chat)  not archived` with the quant indented
beneath it; the record carried `base_model` with
`base_model_source: migrated` and the markdown said so in prose; a
re-run reported nothing to migrate; and `pull` was let through once
converted, proving the gate keys on content rather than a sticky flag.

### Criterion 21 — the live conversion (2026-08-11)

Run by Brian on the real archive after the scoped rehearsal proved the
two live-found fixes. Converted **11 directories, 682.6 GiB**, in
seconds: the moves are renames within one share, so no payload crossed
the wire.

Verified afterwards, all on the real shelf:

- `verify --quick` → **33 models, 33 complete, 0 unmigrated, exit 0**
  (25 before: 3 pure renames are net zero, 8 splits add 8).
- **1126 recorded file entries, 1126 distinct paths** — none claimed by
  two records, none missing from disk. No payload was lost, moved
  twice, or double-counted.
- **Zero empty directories** anywhere under `models/`, and all three
  renamed source directories gone — the nested-removal fix holding on
  four-deep real trees.
- Payload still carries `uchg`: ADR 0001's immutable-payload rule
  survived 682.6 GiB of moves.
- `status` now groups by lineage, and the three directories that were
  named for models the archive does not hold render as
  `(Qwen/Qwen3-Coder-Next)  not archived` with the conversion indented
  beneath — the shelf stating plainly what it holds, which is the
  entire point of ADR 0003.


### Full review round (2026-08-11) — auto-fixes applied

Two reviewers on the complete branch; both returned "do not merge
as-is" and converged on the same defects. Applied here:

- **`save_record`'s schema stamp ran after `write_manifest` serialized
  the record**, so the sidecar committed to bytes that never landed and
  `sha256sum -c` failed — the exact corruption `_commit_record`'s
  docstring claims to prevent, one layer down. Stamping moved into
  `records.io.stamp_current_schema`, called by both writers *before*
  serialization, and changed to `max(...)` so a record from a newer
  tool is never downgraded (which would silence the "newer schema"
  warning that tells a reader unknown fields exist). One directory on
  the live archive was affected; a full `verify` rewrites it.
- **`unlink_locked` left payload permanently unlocked when the unlink
  failed**, contradicting the module's own stated invariant. Restores
  on the failure path now, as `_move_file` already did.
- **`group_by_lineage` listed a model twice** when it was both a base
  and a derivative, double-counting its size in the one column ADR 0003
  relies on. The first fix then *dropped* a grandchild entirely — worse
  — so the rule is now explicit: a model is indented under its base only
  when that base is itself a header, otherwise it heads its own group.
  Every model appears exactly once; chains, orphans, self-references
  and mutual cycles are all pinned.
- **Owner-directory removal was decided per unit**, so two renames
  sharing an owner each saw a sibling and neither claimed the parent.
  Decided once across the whole plan now.
- **The `--view-dest` refresh hint was unquoted** — a dest with a space
  ran against its first word, one starting with a dash arrived as a
  flag (the hazard specs 0007 and 0013 already closed). `shlex.quote`d.
- **The resume hint dropped `--base-model`** while carrying `--role`,
  so an interrupted pull printed a command that lost the curator's
  assertion — the same fault live use found on the no-op path, in the
  other half of the flag's life.
- **`plan_migration` tracebacked** on a model directory whose name is
  not a valid repo id; now a named `MigrateError`.
- **Two guards survived mutation** and now do not: the *core-level*
  content gate in `prepare_pull` (only the CLI half was covered), and
  the identical-claim no-op in `_apply_metadata_only` — whose first
  test compared bytes, which cannot distinguish a no-op from a
  pointless rewrite of identical content. It counts writes now.
- Test debris from pass 3's bulk regex rewrite: a vacuous
  `model_override` test deleted, a `--model` near-duplicate deleted,
  two tests renamed to what they actually pin, and stale comments
  describing deleted flags corrected.

### Executor round (2026-08-11) — the open findings, closed

The reviewers' shared diagnosis drove the shape: five findings were the
*plan* failing to refuse what it could already see, so the executor met
the problem after payload had moved. Those checks moved into the
planner (`migrate/guards.py`, split out at the 300-line cap), which is
what makes `docs/cli.md`'s "refuses as a whole — never half-converts"
true rather than aspirational.

- **Plan-time refusals**: a recorded file absent from *both* source and
  target (an interrupted pull, which `verify` calls `incomplete`) — but
  not one merely absent from the source, which is the resumable case; a
  target that is the source under another spelling, caught with
  `samefile` because macOS does not normalise case in a resolved path;
  and two artifacts claiming one path.
- **A resumed run no longer double-records.** `_merged_artifacts` folds
  on the `(format, source_repo)` key `update_record` already uses.
- **A rename carries the whole record forward** (`model_copy`), not
  five fields off it. The directory it leaves is deleted, so anything
  not carried is destroyed; `pipeline_tag` was lost on the live
  archive's three renames this way.
- **An unusable card `base_model` is dropped rather than fatal.** Real
  cards carry URLs, bare names, `/tree/main` suffixes; feeding one to
  the validator crashed *after* the payload landed and left it with no
  record, permanently unarchivable. The advisory already says the value
  is unusable — declining to record a claim the tool cannot represent
  is the same discipline as never inventing one.
- **Criterion 24 is implemented**: the marker flips only when a fresh
  scan finds nothing unmigrated, so a `--repo` run never claims the
  whole archive is converted.
- **`--to` states what it will copy** (every model directory, not the
  moving artifacts) and writes the requested copy even when there is
  nothing to convert — it is a rehearsal and a backup, not only a
  conversion.

**Superseded by the above** — recorded for the design log:
an unusable card `base_model` crashing `pull` after the payload lands;
a resumed migration double-recording the artifact; a case-insensitive
filesystem collapsing a rename onto itself and deleting the record; a
missing recorded file wedging the archive; a pure rename dropping
record fields the target constructor does not carry (`pipeline_tag`
confirmed lost on the live archive's three renames — a hub fact, so
re-derivable); criterion 24's marker flip never implemented; and
`--to`'s preview describing the source plan while copying the whole
archive. The reviewers' shared diagnosis is worth keeping: five of
these are cases where the *plan* could have refused but the *executor*
discovered the problem after payload had already moved, which is the
one property `migrate` was designed around.
