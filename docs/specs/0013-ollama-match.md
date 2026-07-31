# 0013 — Ollama Match

**Status:** draft
**Last updated:** 2026-07-31

## Goal

Meet users who arrive holding an Ollama model name and get them to the
right archive action mechanically. Two halves, one theme. First, the
matcher: a user running a model from Ollama's own store (`bge-m3:latest`)
has no way to know which hub GGUF repo actually contains those bytes —
name search surfaces several candidates, and picking wrong archives a
*different* build whose embeddings/outputs subtly diverge. Ollama's
store is content-addressed (the local manifest records the model
layer's SHA256 of the GGUF file bytes) and Hugging Face publishes
per-file SHA256s (LFS metadata), so byte-identity is checkable with
metadata calls alone — a hub fact, not tool judgment. Second, the
error-path courtesy (deferred from spec 0011): when an Ollama-shaped
id is pasted into `pull`, say what it is and point at the command that
helps, instead of only "not a valid repo id". Proven by hand
2026-07-31: the local `bge-m3:latest` blob digest matched
byte-identical files in two hub repos, while a third repo's same-size
f16 carried a different digest (different converter run) — exactly the
trap the annotation prevents.

## Success criteria

- `discover` grows a `--match-ollama <name[:tag]>` mode: it reads the
  model-layer digest from the local Ollama manifest (no network, no
  Ollama server needed), runs the normal hub name search for
  candidates, fetches candidate repos' file listings (metadata only —
  no payload downloads, no hashing), and prints each candidate GGUF
  with an explicit verdict: **byte-identical** (SHA256 equal to the
  local blob), or unverified (with size shown, so a same-size
  different-digest file is visibly a near-miss, not a match). The
  search term defaults to the Ollama model name with the tag stripped
  (e.g. `bge-m3`), overridable by passing an explicit search term
  alongside the flag (confirmed 2026-07-31).
- The match annotation is a stated fact, never a ranking: candidates
  print in the hub's order as elsewhere in `discover`, the human still
  picks, and the tool never auto-pulls.
- Every byte-identical match prints the exact pasteable archive
  command (confirmed in scope, 2026-07-31), resume-hint style: the
  matched repo id and the matched filename as a quoted `--include`
  (e.g. `llm-preserver pull gpustack/bge-m3-GGUF --include
  'bge-m3-FP16.gguf'`), composed under the 0007 rules — repo id
  validated at composition, every token sanitized then quoted. The
  human pastes it (optionally adding `--model` grouping); the tool
  still never runs it.
- The local Ollama store is read-only to this feature: the manifest
  is read from `$OLLAMA_MODELS` when set, else Ollama's default
  location; nothing under it is ever written, and a missing/unreadable
  store or unknown model name is a clean exit-2 error naming what was
  looked for and where.
- No byte-identical candidate found is a clear, non-error outcome:
  the listing still prints (with sizes), states that no exact match
  was verified, and notes that archiving a near-miss archives a
  different build. Exit 0 — the scan succeeded and reported facts;
  the absence of a match is information, not failure (confirmed
  2026-07-31).
- `pull` with an Ollama-shaped id (deferred from spec 0011): the
  existing clean invalid-id error gains shape detection —
  `name:tag` adds "this looks like an Ollama model name; it has no
  direct Hugging Face equivalent — try `discover <name>` or
  `discover --match-ollama <name:tag>`", and `hf.co/<org>/<repo>[:<quant>]`
  adds the exact mechanical translation (`pull <org>/<repo>`, with the
  quant shown as an `--include` hint). Detection lives in the error
  path only; no id is ever rewritten or auto-corrected, and exit
  codes are unchanged (still 2).
- All hub interaction goes through the existing hub seam (extended if
  needed for per-file LFS hashes); everything is testable with the
  fake hub client plus a fake Ollama store in `tmp_path`.

## Non-goals

- No pulling from Ollama's registry — the hub remains the only
  acquisition source; this maps *to* hub repos, nothing more.
- No fuzzy or heuristic matching: byte-identity (SHA256 equality) is
  the only "match" verdict the tool ever states. Near-misses are
  displayed facts, never scored or recommended.
- No auto-pull on match, no auto-selection of a repo — the human
  picks, per the design stance (no LLM, no tool judgment).
- No modification of the Ollama store, and no dependency on a running
  Ollama server (manifest files are read directly).
- Not a general reverse-lookup service: hf.co has no search-by-hash
  API, so matching is only as good as the name-search candidates; a
  model whose repos use unrelated names may find no candidates. That
  limitation is documented, not worked around with heuristics.

## Notes

- Hard-won facts from the by-hand run (2026-07-31, recorded in
  TODO.md): local manifest at
  `$OLLAMA_MODELS/manifests/<registry>/<namespace>/<model>/<tag>`
  carries the model layer's `sha256:` digest = SHA256 of the GGUF
  bytes; HF's repo tree API exposes `lfs.oid` (the file SHA256) per
  file; `bge-m3:latest` matched byte-identical files in
  `gpustack/bge-m3-GGUF` and `lm-kit/bge-m3-gguf`, while
  `CompendiumLabs/bge-m3-gguf`'s f16 was same-size different-digest.
- The hub seam likely needs a per-file-hash listing capability;
  `huggingface_hub`'s repo-tree API carries LFS SHA256s. Verify at
  implementation time per the external-reference provenance rule.
- Ollama's manifest tree may move (open PR #15735, "manifest-v2" —
  tracked in spec 0002's external references); the manifest-path
  constant should be shared with or mirrored from the views adapter's
  provenance-pinned constants rather than duplicated blind.
- Depends on nothing unshipped: `discover` (0006) provides search and
  the pull handoff; 0011 provides the clean invalid-id error path the
  shape detection extends.
- Sizing: likely two small phases on one branch (matcher; error-path
  detection) or a single medium branch — decide at `/plan`.
