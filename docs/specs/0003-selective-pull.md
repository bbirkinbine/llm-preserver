# 0003 — Selective Pull

**Status:** shipped
**Last updated:** 2026-07-12

## Goal

Add the first download command: pull *selected* files from a Hugging
Face repo into the archive, preserving everything spec 0001's record
schema was built to hold. Input is an exact hub repo id — the tool
never resolves fuzzy names ("qwen 27b"); deterministic metadata
lookups only, no LLM in the tool. A repo id (`org/name`) names a
repository, not a version: versions are git revisions within it, which
is why every pull records the resolved commit hash — repo id is the
address, commit pin is the version. The user picks the artifacts that
fit their hardware (e.g. one Q4_K_M file from a 20-quant GGUF repo,
never all of them); the tool assists by listing the repo's files with
sizes (one metadata API call) for interactive or `--include`-pattern
selection. Alongside the chosen weights, the pull always fetches the
repo's README / model card and LICENSE, records SHA256 checksums and a
pinned commit hash, and writes/updates the model record (JSON +
rendered markdown via `save_record`). This is the feature that makes
the archive an archive; runtime views (spec 0002) and verify ship
after it.

## Success criteria

- `llm-preserver pull <repo-id>` against an initialized archive
  downloads the selected files into the canonical model directory,
  under the format subdirectory ADR 0001 defines. (Command name
  `pull` decided 2026-07-10 — matches the roadmap's language and the
  `ollama pull` / `docker pull` mental model; full snapshot later
  rides the same verb.)
- Given a repo id and no selection flags, the tool lists the repo's
  files with sizes (one metadata API call) and prompts for selection;
  `--include <pattern>` selects non-interactively. A pull that would
  fetch every weight file in a multi-quant repo requires explicit
  confirmation.
- README / model card and LICENSE files are fetched alongside the
  selected artifacts on every pull.
- The resolved commit hash (not a branch name) is recorded — a branch
  is a moving pointer, not provenance (ADR 0001).
- Every downloaded file gets a SHA256 in the record. Artifacts whose
  locally computed hash matches the hub-declared hash are
  `provenance: verified`; files the hub publishes no hash for (some
  non-LFS small files) are `provenance: hashed-locally` — bytes came
  straight from the hub, our SHA256 is recorded, but no independent
  check was possible (decided 2026-07-10; keeps `verified` strictly
  honest and extends spec 0001's provenance states, bumping
  `record_schema_version` if the schema gate requires it). Provenance
  is recorded *per file* (`FileEntry`), and the artifact-level state
  is derived: `verified` iff every file verified, else
  `hashed-locally` — so a hash-less README cannot demote verified
  weights, and `verified` stays strict (plan decision, 2026-07-10).
- Pull records hub facts without prompting: the repo's
  `pipeline_tag` lands verbatim in its existing record seat.
  `capabilities` stays unpopulated in this spec — no deterministic
  derivation vocabulary exists yet, and inventing one would
  fabricate machine facts; a later feature with a real source
  (e.g. GGUF-embedded metadata) populates it (test-first finding,
  2026-07-10). `roles` (curator
  judgment) becomes optional-empty — amending spec 0001's
  required-nonempty rule via the same schema-v2 bump; the tool never
  fabricates judgment from hub labels. An optional `--role` flag
  assigns at pull time; `status` groups role-less models under a
  visible "(no role)" bucket (decided 2026-07-10).
- After files move into the archive, pull writes/refreshes the
  model's `manifest-sha256.txt` and clears write permission
  (`chmod a-w`) on payload files — honoring accepted ADR 0001's
  payload-locking mandate in the first feature that writes payload
  (decided 2026-07-10).
- Pulling a quant repo (e.g. `bartowski/...-GGUF`) infers the
  canonical model directory from the quant repo's `base_model`
  model-card metadata, confirms the grouping with the user, and
  accepts a `--model` override (ADR 0001, "judgment call at download
  time"). No metadata and no override is a hard stop, not a guess.
- Re-pulling into an existing model directory updates the one record
  that spans formats without clobbering artifacts recorded by earlier
  pulls.
- Pulls are idempotent by content hash: a selected file whose
  hub-declared SHA256 already matches an artifact in the record is
  skipped with an "already archived" report — no re-download. For
  files the hub publishes no hash for, the skip falls back to a
  name + size match against the record (best effort — content
  identity can't be known without downloading). A
  selected file whose *name* matches a recorded artifact but whose
  hash differs (the repo updated it) is a hard stop showing both
  hashes; replacing or adding the new content requires an explicit
  choice, never a silent overwrite (the archive is
  payload-immutable). The skip requires the record match *and* the
  file present on disk (existence check only — no re-hash): a
  recorded artifact whose file is missing means the archive was
  damaged outside the tool, and pull warns about the drift and
  re-downloads rather than trusting the stale record. Full drift
  detection (completeness + hash validity across the whole archive)
  is the planned verify feature's job, not pull's.
- A failed or interrupted download leaves no partial file recorded as
  an artifact — the record only ever describes bytes that are fully
  on disk and hashed.
- The archive-path argument on every command falls back to the
  `LLM_PRESERVER_ARCHIVE` environment variable when omitted; an
  explicit path always overrides it, and neither set is a usage
  error. No config file — the archive marker remains the only state
  (added 2026-07-10; scope addition approved by Brian to avoid
  new-spec overhead for a one-line ergonomic). `init` prints a hint
  showing the export line for the just-created archive (absolute
  path), only when the variable is not already set — generic POSIX
  `export` syntax, no shell detection. The archive path is the *last*
  positional on multi-argument commands (`pull REPO_ID [PATH]`,
  `show MODEL_ID [PATH]`): Click binds positionals left-to-right, so
  an omittable path must trail — found live by Brian when
  `pull <repo>` misbound the repo id to PATH (fixed 2026-07-11).
- A failed pull exits nonzero with a message that names the fault
  domain — user input (unknown repo id, gated repo not accepted),
  local environment (network unreachable, disk full), hub-side
  (5xx, rate limiting, maintenance), or integrity (SHA256 mismatch
  after a completed download) — so a human or an agent can triage
  without reading source. Each domain's message states the likely
  next step; integrity failures are reported distinctly and are
  never silently retried into the archive. Tests simulate each
  failure class against the mocked client and assert the
  classification.
- All of the above covered by tests that hit no network (hub API
  mocked/faked); `/review-check` green.

## Non-goals

- **Full snapshot** — whole-repo-tree download is its own spec (next
  in the roadmap). This spec is the selected-files shape only. The
  split is download *shape* and UX, not mechanism: full snapshot is
  expected to reuse this spec's download/checksum/record machinery
  and differ in selection (whole tree), confirmation math
  (hundreds-of-GB pulls), and grouping (an original repo *is* the
  canonical model; an `mlx-community/*` repo routes into an existing
  model's `mlx/` subdirectory — no `base_model` inference step).
- **Fuzzy name resolution or any LLM inside the tool** — exact repo
  ids only (0000 design stance).
- **Verify / re-hash of the existing archive** — separate roadmap
  item; this spec only hashes what it downloads.
- **Cache import** (Ollama / LM Studio) — separate roadmap item.
- **Hubs other than Hugging Face.** This spec is HF-only, by
  decision (2026-07-10): HF is the canonical registry for
  open-weight LLMs and their quant ecosystem, so it covers nearly
  everything this tool exists to preserve. The record schema from
  spec 0001 is already hub-agnostic (`source_repo` is a URL,
  provenance is per-artifact), so a second hub later is a new fetch
  backend behind its own spec — justified only by a real model that
  cannot be pulled from HF, not researched up front. Ollama library
  content is deliberately not a pull backend; it enters the archive
  via the planned cache-import feature.
- **Runtime views** — spec 0002, ships after the download specs.
- **Mirroring every quant in a repo by default** — selection is the
  point; bulk-pull convenience flags can come later if ever.
- **Deleting or modifying archive contents.** Pull only ever adds:
  it never removes, moves, or rewrites archived payload (don't-touch
  rule — the archive is irreplaceable output). Repairing metadata
  after out-of-band damage is verify's territory; a managed
  remove/retire command that keeps record and directory consistent
  is a future spec of its own (0000 roadmap), never a side effect of
  a download feature.

## Notes

- Depends on spec 0001 (shipped): archive marker/schema gate, record
  models, `save_record`.
- Adds the `huggingface_hub` dependency — run the
  `dependency-hygiene` skill when it lands in `pyproject.toml`.
  Downloads go through the official client by decision (2026-07-10):
  it handles CDN redirects, retries, the Xet storage backend, and
  native resume; raw HTTP against the hub is a non-goal. The one
  metadata call (`model_info` with per-file metadata) supplies file
  sizes for selection *and* hub-declared LFS sha256s for `verified`
  provenance. Downloads land in a staging directory (the client's
  `local_dir` mode keeps `.cache/huggingface/` bookkeeping we must
  not archive), are hashed there, then move into the archive. API
  facts verified against the installed 1.x client and official docs
  (2026-07-10) — see `## External references`.
- Client-churn posture (2026-07-10): `uv.lock` pins the client and
  Dependabot + CI gate every bump; the implementation must isolate
  all `huggingface_hub` calls behind one thin module so upstream API
  changes localize to a single seam. The archive itself never
  depends on the client — records carry source URL, pinned commit,
  and SHA256s, so archived models stay verifiable and re-acquirable
  by any future client. Reading the HF *cache directory* instead of
  driving the API was considered and rejected as the primary path:
  the cache layout is an internal contract (already shifting with
  the Xet backend), and only an API-driven pull lets us verify
  hashes, pin the commit, and guarantee license/model-card
  completeness at download time. The HF cache is instead a candidate
  third *import* source under the planned cache-import feature.
- Auth (decided 2026-07-10): reuse Hugging Face's ambient token
  discovery — `HF_TOKEN` env var or the token file written by
  `hf auth login`. The tool passes no token arguments, adds no token
  flags, stores nothing, and never prints or logs the token; gated
  and private repos work iff the user has logged in with HF's own
  tooling. This is the security posture, not just convenience:
  no token handling in our code means nothing to leak.
- Logging (decided 2026-07-10): stdlib `logging` (already the
  planned stack — no new dependency), established by this spec since
  it is the first network surface. Default output is concise; a
  `--verbose` flag raises the level to show per-file progress,
  resolved commit, staging paths, and the underlying client
  exception on failure. Fault-domain classification maps the typed
  exceptions `huggingface_hub` raises rather than parsing message
  strings. The `Authorization` header / HF token value must never
  appear at any log level (public-repo hygiene rule); `/security`
  should probe this. No log files or telemetry — console only;
  later features inherit the same convention.
- ADR 0001 flags that one record spans formats, so two concurrent
  pulls into the same model could race on `model-record.json`;
  single-user CLI, non-forcing, but the write path should stay
  last-write-wins-safe. Worth a test, not a lock.
- Resume (decided 2026-07-10): delegated to `huggingface_hub`'s
  native retry/resume — we build no resume machinery of our own; our
  invariant is only that the record is written after a file is fully
  downloaded and hashed, never before. Verified against 1.x docs:
  within a call, transient network failures are retried with HTTP
  Range resume; a *killed process* restarts the in-flight file from
  zero on the next run (process-unique temp files). Completed files
  in staging are still reused, so re-running the same `pull` loses
  at most the one in-flight file.

## Review adjudications (2026-07-10, Brian)

Five review findings challenged spec decisions; ruled as follows:

- **Doc files get a collision-free home and an explicit refresh
  path; weights stay absolutely immutable.** Docs (README / model
  card / LICENSE / use-policy) land under
  `<format>/docs/<source-namespace>--<source-repo>/` so two source
  repos can never collide on `README.md`, and a new
  `--refresh-docs` flag is the spec's "explicit choice" for
  replacing changed upstream docs (unlock, replace, re-record,
  re-lock, manifest refresh — doc paths only, never weights). A
  changed *weight* remains a hard stop with no override.
- **Unrecorded on-disk files reconcile by hash.** A file found on
  disk but absent from the record (crash between move and
  record-write) is hashed; if it matches the hub-declared hash it is
  adopted into the record and the pull continues — the
  refuse-forever stop remains for any mismatch.
- **Per-file revision.** `FileEntry` gains an optional `revision`
  (40-hex commit), recorded at download; the artifact-level
  `revision` is the most recent pull's commit. A merged artifact no
  longer implies old files were checked against a newer commit.
- **Re-pull merge never erases hub facts.** `pipeline_tag` (like
  `license`) is only overwritten by a non-null value.
- **v1 artifacts merge by format.** An existing artifact with
  `source_repo: null` and a matching format is the merge target (the
  source is filled in) rather than a duplicate being appended.

## External references

All retrieved 2026-07-10 against `huggingface_hub` 1.23.0 (installed
package introspection + official docs). Key 1.x corrections vs.
prior memory: the client uses httpx (not requests);
`RemoteEntryNotFoundError` is the HTTP file-404 error
(`EntryNotFoundError` is a plain base); 401 maps to
`RepositoryNotFoundError` by design; rate limiting has no dedicated
class (`HfHubHTTPError` with `response.status_code == 429`); a killed
process restarts the in-flight file from zero.

- "HfApi Client" —
  <https://huggingface.co/docs/huggingface_hub/package_reference/hf_api>
  — `model_info(files_metadata=True)`; `ModelInfo.sha`,
  `.pipeline_tag`, `.card_data` (`.base_model`, `.license`);
  `RepoSibling.rfilename`/`.size`/`.lfs`; `BlobLfsInfo.sha256`
  (`None` for non-LFS files).
- "Download files from the Hub" —
  <https://huggingface.co/docs/huggingface_hub/guides/download>
  — `hf_hub_download(..., revision=..., local_dir=...)`;
  `<local_dir>/.cache/huggingface/` bookkeeping (safe to delete
  after success); Xet-backed transfers.
- "Utilities" —
  <https://huggingface.co/docs/huggingface_hub/package_reference/utilities>
  — error classes and hierarchy: `HfHubHTTPError(httpx.HTTPError,
  OSError)` with `.response`/`.server_message`;
  `RepositoryNotFoundError` (401/404), `GatedRepoError` (403,
  subclass), `RevisionNotFoundError`, `RemoteEntryNotFoundError`,
  `LocalEntryNotFoundError`, `OfflineModeIsEnabled`; "always resumes
  downloads whenever possible" (within-call Range retry).
- "Environment variables" —
  <https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables>
  — `HF_TOKEN` env var; token file `HF_TOKEN_PATH` (default
  `$HF_HOME/token` = `~/.cache/huggingface/token`).
- "Quickstart" —
  <https://huggingface.co/docs/huggingface_hub/quick-start>
  — `hf auth login`; token precedence (env over file).
- "v1.0: Building for the Next Decade" —
  <https://github.com/huggingface/huggingface_hub/releases/tag/v1.0.0>
  — requests→httpx migration; `huggingface-cli` removed in favor of
  `hf`.
