# 0002 — Runtime views

**Status:** draft
**Last updated:** 2026-07-09
**Depends on:** 0001

## Goal

Generate disposable, tool-specific "views" — symlinks and config —
that let inference runtimes run models *in place* from the archive,
because models are too large to shuttle between bulk storage and
local disks. The archive stays the single, payload-immutable copy
(ADR 0001 → "Runtime consumption: views, not copies" owns that
principle); this spec owns the per-tool mechanics. It ships after the
download specs exist, since views need models to point at — the
number is an identifier, not an execution order.

## Success criteria

- `llm-preserver views <archive> --tool lm-studio --dest <dir>`
  builds a local view tree of `<publisher>/<model>/<file>.gguf`
  symlinks into the archive, matching LM Studio's required two-level
  layout. Re-running refreshes the tree; deleting it loses nothing.
- `llm-preserver views <archive> --tool llama-cpp` (and vLLM
  equivalent) prints/exports the direct archive paths per model —
  these tools need no links, only path discovery.
- An Ollama mode exists only as explicitly-labeled **best effort**:
  either emit instructions for a local working copy, or (opt-in flag)
  generate the community blob-symlink layout using the SHA256s
  already in the records (no re-hashing) with a loud warning that
  Ollama does not support it and pruning may break it.
- Views never write into the archive, and view generation works
  against a read-only archive mount.
- Every view file/dir is identifiable as generated (naming or marker)
  so nothing mistakes a view for archived data.

## Non-goals

- Not a model server or launcher — views make runtimes *able* to find
  the archive; starting/serving models stays the runtime's job.
- No hot-cache management (copying frequently-used models to local
  disk is the human's call; the design permits it, this spec doesn't
  automate it).
- No contorting the archive layout to match any single tool (rejected
  in ADR 0001).

## External references

Per-tool feasibility verified 2026-07-09 (research session; moved
here from ADR 0001):

- **llama.cpp — direct path.** `llama-cli`/`llama-server` `-m` takes
  any path, read-only OK, mmap by default. Source:
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.
  Community-practice caveat (not official guidance): mmap page-in
  over NFS/SMB can be slow or hang on very large models; `--no-mmap`
  or direct-I/O are the mitigations.
- **vLLM — direct path.** `vllm serve <hf-snapshot-dir>` with
  `HF_HUB_OFFLINE=1`; matches the archive's `hf-snapshot/` contents.
- **LM Studio — config redirect + file-level symlinks.** Models dir
  is user-changeable; layout must be exactly
  `<publisher>/<model>/<file>.gguf`. File-level symlinking is
  officially supported via `lms import --symbolic-link` (flags seen
  via search snippets of
  <https://lmstudio.ai/docs/cli/local-models/import>; page not fully
  fetched). **Warning:** bare `lms import` *moves* the file — never
  run it against the archive. Layout source:
  <https://lmstudio.ai/docs/app/advanced/import-model>.
- **Ollama — no supported in-place mode.** `OLLAMA_MODELS` requires
  read-write access (Ollama FAQ, search snippet); `ollama create
  FROM /path.gguf` copies across filesystems
  (<https://docs.ollama.com/import> is silent; behavior per community
  sources). Blob-symlink workaround (`blobs/sha256-<digest>` →
  external file) is community-known and unsupported:
  <https://github.com/ollama/ollama/issues/1981> (feature request,
  closed unimplemented). Cross-tool linking practice:
  <https://www.rushis.com/sharing-local-llm-models-between-ollama-and-llama-cpp/>.

**Unverified — must be tested during implementation, not assumed:**
LM Studio dir-level symlinks and read-only models-dir tolerance;
Ollama prune behavior against symlinked blobs; llamafile/mlx-lm path
handling. Re-verify all tool behaviors at implementation time — these
tools version fast and this spec's research will be stale.

## Sketch

A `views` module with one small adapter per tool, each consuming the
archive records (never re-hashing payloads). Symlink targets use
absolute paths into the archive mount; document the stable-mount-point
assumption. Tests build a fake archive in `tmp_path` and assert view
shape, link targets, and that the archive tree is untouched
(read-only bit respected).
