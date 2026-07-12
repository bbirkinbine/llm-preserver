# TODO

What's next, in rough order. Feature detail lives in
[`docs/specs/0000-product.md`](docs/specs/0000-product.md) (roadmap)
and the numbered specs; this file is the short-term working list.
Check items off as they ship; update when priorities shift.

## Next spec (0005) — pick one

- [ ] **Verify** (recommended next): audit the archive against
  records/manifests, BagIt-style — *complete* (every recorded file
  exists) and *valid* (every file re-hashes to its recorded SHA256).
  Read-only report. The archive now holds real Tier-1 content, so
  drift/bitrot detection is earning its keep.
- [ ] **Runtime views** (spec 0002, drafted): symlink/config views so
  runtimes run archived models in place. Its blocker (the download
  specs) is lifted — this is what makes the archive *usable* daily.
- [ ] **Managed remove/retire**: the only sanctioned way to delete
  from the archive (record + directory updated together). Real
  pruning needs exist from first live use.
- [ ] **Smoke test**: load an archived model offline in a local
  runtime (llama.cpp / ollama), check a trivial deterministic
  prompt, record the result in the record's `runtime_tested` field
  (a 0000 success metric: the archive is *tested*, not just
  downloaded). Pairs with runtime views — views make models
  loadable in place, smoke test proves they load.

## Shipped

- 0001 archive init + records, 0003 selective pull, 0004 full
  snapshot (`pull --all`). The core loop works end to end and is
  live-verified: init → pull quants and masters → status/show.

## Smaller items (from live use)

- [ ] Quant-label selection UX: annotate recognized quant labels in
  the interactive listing (deterministic table) and/or `--quant`
  sugar. In the 0000 roadmap "Later" list.
- [ ] Companion-artifact advisory: a curated rules table (data, not
  inference — no LLM in the tool) mapping repo-tree filename patterns
  to artifact kinds: `mmproj-*` → vision projector, `mtp-*` →
  speculative-decoding head, `*imatrix*` → quantization calibration
  data, shard suffixes → incomplete-set check. Cross-repo deps come
  from machine-readable metadata (`adapter_config.json` →
  `base_model_name_or_path`). At pull time, when the tree ships a
  known companion kind the selection excludes — or an adapter's base
  model is absent from the archive — print an advisory naming the
  exact `--include` / follow-up pull; advisory only, never auto-add.
  Generalizes the vision-companion advisory from the 0000 roadmap
  "Later" list (live-hit 2026-07-12: gemma-4-31B-it Q4_K_M pull
  omitted `mmproj-F16.gguf` until a human noticed).
- [ ] `pull --plan` (dry run): resolve the repo tree, apply
  `--include` / `--all` selection and the grouping rules, then print
  what *would* happen — selected files with sizes and total, the
  canonical model directory, docs that ride along, already-archived
  skips, and any companion-artifact advisories — and exit without
  downloading or writing. Turns scripted pulls from "hope the
  pattern is right" into "verify, then run"; pairs with the
  companion-artifact advisory above.
- [ ] Example-run cookbook (`docs/examples.md`): one worked pull per
  repo archetype — GGUF quant repo, original safetensors (`--all`),
  multimodal (weights + `mmproj`), sharded weights, adapter/LoRA,
  embedding/reranker, gated repo (`hf auth login`). Each example
  shows the non-interactive form (`--include` + `--model` + `--yes`)
  so scripted/cron runs have a copy-paste recipe per model type.
- [ ] `quantization` record field is never populated (artifact-level
  label extraction was never specced; per-file is likely the right
  shape now that one artifact can hold several quants).
- [ ] Split `pull.py` (291) and `records.py` (296) — both near the
  300-line cap; split before the next feature touches either.
- [ ] Refresh the CLAUDE.md "Open work" session notes (still
  describe 0004 as pending).
