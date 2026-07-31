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
  is read from `$OLLAMA_MODELS` when set (an explicit override, never
  a fallback chain), else the first existing of `~/.ollama/models`
  and the Linux system-install root
  `/usr/share/ollama/.ollama/models` — a fixed documented probe
  order, with the chosen store disclosed in the output (adjudicated
  2026-07-31: a stock Linux service install must work out of the
  box). Nothing under the store is ever written, and a
  missing/unreadable store or unknown model name is a clean exit-2
  error naming what was looked for and every path checked.
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

## External references

Values whose correctness depends on an external authority; all
fetched in-session and pinned with a provenance comment where the
value is defined (`src/llm_preserver/ollama_layout.py` unless noted).
Ollama is MIT-licensed — consulted, never copied.

- Default store root (`~/.ollama/models`; Linux system installs use
  `/usr/share/ollama/.ollama/models`) and the `OLLAMA_MODELS`
  override: https://docs.ollama.com/faq, fetched 2026-07-31.
- Name defaults for omitted components (registry `registry.ollama.ai`,
  namespace `library`, tag `latest`): github.com/ollama/ollama
  `types/model/name.go` (`defaultHost` / `defaultNamespace` /
  `defaultTag`), fetched 2026-07-31.
- Manifest tree layout (`manifests/<registry>/<namespace>/<model>/<tag>`)
  and the model-layer media type
  (`application/vnd.ollama.image.model`): github.com/ollama/ollama
  `manifest/paths.go` and `manifest/layer.go`, fetched 2026-07-30
  during spec 0002, confirmed against a real store on this machine;
  constants shared with the views adapter via `ollama_layout.py`.
  Watch: open Ollama PR #15735 ("manifest-v2") would move the
  manifest tree.
- Per-file SHA256s from the hub: the existing seam's
  `RepoFile.sha256` (LFS metadata via `huggingface_hub` repo tree,
  provenance pinned with spec 0003's references) — verified at
  implementation time; no new hub surface was added.

## Notes

- **Live-verified 2026-07-31** on the real store and real hub:
  `discover --match-ollama bge-m3:latest` read the local digest with
  no server, listed 20 candidates in hub order, marked exactly
  gpustack/bge-m3-GGUF's FP16 byte-identical, and every other GGUF
  (including same-family quants) unverified. (lm-kit/bge-m3-gguf, the
  second by-hand match, sat outside the hub's first 20 for `bge-m3` —
  the disclosed first-page cap, working as designed.)
- **Output-shape adjudication (Brian, 2026-07-31, from that run):**
  the first rendering buried the one actionable line in twenty
  candidate blocks ("lost in a wall of text"). Repos with no GGUF
  files now roll up into a single summary line, and the pasteable
  command moved to a footer as the final output line ("run this to
  archive it:"), per the 0007 stance that the line to paste sits
  directly above the next prompt.
- **Depth adjudication (Brian, 2026-07-31):** `--limit <n>` (max 500,
  default the first search page) pages deeper for models whose
  byte-identical repo ranks low. Evidence from the live run: the
  second true bge-m3 match (lm-kit) sat beyond the first 20 results.
  Default stays shallow because every candidate costs one hub
  metadata call; an unbounded `--all` was rejected (rate-limited API,
  no benefit over a large explicit number). The 500 ceiling held on
  re-examination after a live `--limit 500` run found six matches:
  byte-identical matches are the same bytes, so one hit suffices —
  depth past 500 only matters at zero matches, where a sharper
  `--search` is the stronger lever.
- **Depth-run adjudications (Brian, 2026-07-31, from the live
  `--limit 500` run):** the no-GGUF roll-up truncates at ten names
  plus a count (440 names was a wall of its own), and a multi-match
  footer says "all the same bytes; run any ONE" — six identical
  commands must not read as six required pulls. Each footer match
  also carries the repo's hub facts (downloads · last-modified ·
  gated, via the shared `summary_facts` renderer): identical bytes
  leave uploader provenance as the only pick criterion, and those
  are the facts to pick with. Facts, never a ranking — order stays
  the hub's.
- **Store-root adjudication (Brian, 2026-07-31):** fixed-order probe
  (`$OLLAMA_MODELS` as sole candidate when set, else user-local root,
  else the Linux system-install root), chosen store disclosed; and
  `--plan` is refused in match mode rather than silently ignored.

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
