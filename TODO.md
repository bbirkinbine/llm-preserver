# TODO

What's next, in rough order. Feature detail lives in
[`docs/specs/0000-product.md`](docs/specs/0000-product.md) (roadmap)
and the numbered specs; this file is the short-term working list.
Check items off as they ship; update when priorities shift.

## Next spec (0006) — pick one

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
- [ ] **Guided discovery workflow** (product call first): from a
  model name, list the hub model tree's quant children
  (deterministic `base_model` metadata, no LLM), pick the exact repo,
  then the normal interactive selection. Revisits the
  exact-repo-ids-only stance — needs a 0000 adjudication before a
  spec.

## Shipped

- 0001 archive init + records, 0003 selective pull, 0004 full
  snapshot (`pull --whole-repo`, shipped as `--all` and renamed by
  0005). The core loop works end to end and is live-verified: init →
  pull quants and masters → status/show.
- 0005 companion advisories + `pull --plan` (merged 2026-07-13,
  rebase-merge): archive-aware advisory rules (companions, shard
  sets, adapter base, full-precision master, `--model` grouping
  mismatch as a highlighted warning), the `--plan` dry run,
  `--all` → `--whole-repo`, size confirmation + disk preflight on
  every pull mode. Live-verified against real Qwen3.6 repos,
  including the copy-pasted `--model` footgun it now catches.

## Smaller items (from live use)

- [ ] Quant-label selection UX: annotate recognized quant labels in
  the interactive listing (deterministic table) and/or `--quant`
  sugar. In the 0000 roadmap "Later" list. Live-use additions
  (2026-07-12): the listing prints raw byte counts where the plan
  report prints human sizes — use `human_size` there too; empty
  pattern input at the prompt errors (exit 2) instead of
  re-prompting.
- [ ] Example-run cookbook (`docs/examples.md`): one worked pull per
  repo archetype — GGUF quant repo, original safetensors
  (`--whole-repo`),
  multimodal (weights + `mmproj`), sharded weights, adapter/LoRA,
  embedding/reranker, gated repo (`hf auth login`). Each example
  shows the non-interactive form (`--include` + `--model` + `--yes`)
  so scripted/cron runs have a copy-paste recipe per model type.
  The `--plan` flag belongs in every recipe as the verify step.
- [ ] `quantization` record field is never populated (artifact-level
  label extraction was never specced; per-file is likely the right
  shape now that one artifact can hold several quants).
- [ ] Split `records.py` (296) — near the 300-line cap; split before
  the next feature touches it (`pull.py` was split by 0005).
