# 0022 — Hugging Face Client Compatibility

**Status:** shipped (PR #40)
**Last updated:** 2026-09-21

## Goal

Make the supported `huggingface_hub` contract explicit, move the repository
off a client version that predates a relevant `local_dir` path-containment
security fix, and give future updates enough evidence to be routine rather
than speculative. The installable project supports `huggingface_hub` 1.x from
the security floor through the newest compatible 1.x release, but must not
admit an unqualified 2.x release: a major-version boundary is treated as
potentially breaking until this repository deliberately tests and adopts it.
The deterministic suite will pin the client calls and response fields the hub
seam consumes, while a small scheduled live-Hub canary will detect server-side
API and metadata drift that fakes and a lockfile cannot.

This spec both hardens the compatibility boundary and updates the locked
client from 1.24.0 to the currently qualified 1.32.0 release. Later 1.x
updates return to normal Dependabot or `chore/` branches and must pass the
guardrails established here.

## Success criteria

- The runtime dependency is declared as `huggingface-hub>=1.26.0,<2`.
  Installing the project outside its development lockfile therefore cannot
  resolve to `huggingface_hub` 2.x. Version 1.26.0 is the minimum because it
  is the first release that rejects absolute, drive-relative, UNC, and
  parent-traversal filenames before a `local_dir` download writes them.
- `uv.lock` pins `huggingface_hub` 1.32.0 for reproducible local development
  and ordinary CI. The declared lower bound is not raised merely because a
  newer 1.x release exists; raising it requires either a security floor, use
  of a newer-only API, or an explicit decision to drop the older supported
  version.
- A focused, network-free compatibility suite exercises the real production
  adapter in `llm_preserver.hub`, rather than only downstream protocol fakes,
  and fails if any of these contracts change incompatibly:
  - `HfApi.model_info(repo_id, files_metadata=True)` and the consumed commit,
    sibling, LFS SHA256, pipeline-tag, license, and base-model fields;
  - `hf_hub_download(repo_id=..., filename=..., revision=...,
    local_dir=...)` and its returned local path;
  - `HfApi.list_models` search, child-filter, download-sort, expansion-field,
    and lazy-iteration behavior, plus `model_info(..., expand=...)` for parent
    lookup;
  - every Hugging Face exception and logging symbol on which fault-domain
    mapping or `--hf-logging` depends.
- Compatibility checks can run against both the declared floor (`1.26.0`)
  and the newest resolvable `<2` release without rewriting the main lockfile.
  Their output names the resolved client version so a failure identifies the
  environment that produced it.
- Ordinary pull-request CI remains reproducible against `uv.lock`. A separate
  scheduled and manually dispatchable compatibility workflow runs the
  floor/latest checks and a live-Hub canary weekly.
- The live canary uses public `openai-community/gpt2` and no credentials or
  repository secrets. It exercises the real metadata/discovery path and
  downloads only the 665-byte `config.json` at immutable revision
  `607a30d783dfa663caf39e06633721c8d4cfcd7e` into ephemeral runner storage,
  so both `HfApi` and `hf_hub_download` are covered without touching a user's
  archive.
- A canary failure distinguishes client-install/import failure, metadata or
  discovery failure, and download failure in its step names and logs. It does
  not report configuration success as proof that a live Hub operation worked.
- Production code does not import an undeclared transitive dependency as if
  it were part of this project's public dependency contract. In particular,
  the raw `httpx` exception handling is backed by the direct runtime
  dependency `httpx>=0.23,<1`, matching the compatible transport range of the
  supported Hugging Face 1.x clients. The Hugging-Face-owned `httpx` re-export
  introduced after the 1.26.0 floor cannot be the unconditional import.
- Maintainer documentation explains how to evaluate an HF update: inspect all
  intervening official release notes, run the locked full gate and advisory
  audit, run the floor/latest compatibility checks, and complete the live
  canary before merging. Authentication-specific live verification is added
  when release notes touch auth, tokens, gated repos, or HTTP transport.
- `pip-audit` reports no known advisory in the resulting locked dependency
  tree. The lock refresh must not leave the currently reported `anyio` 4.14.1
  advisories unresolved; `anyio` remains transitive rather than becoming a
  new direct dependency solely to force that result.
- The existing full gate remains green, and the new tests are shown red
  against deliberate mutations that remove or alter the compatibility
  behavior they claim to protect.

## Non-goals

- Supporting or pre-adapting to `huggingface_hub` 2.x. A future major-version
  adoption requires its own reviewed work based on the released migration
  guidance and observed behavior.
- Testing every 1.x release between the declared minimum and newest available
  version. The supported endpoints are the floor, the exact locked version,
  and the newest allowed 1.x release.
- Replacing the official Hugging Face client with raw Hub HTTP calls or
  depending on the client's private cache-directory layout.
- Storing an HF token in GitHub Actions or adding a scheduled private/gated
  repository test. Authenticated checks remain explicit update-qualification
  work when the upstream change warrants them.
- Changing archive schemas, model records, CLI output, exit codes, selection
  behavior, or the existing ambient-auth policy.
- Treating one successful canary as a guarantee that every Hub repository or
  future server response will behave identically.

## Notes

- Current state at drafting: `pyproject.toml` declares
  `huggingface-hub>=1.24.0` with no upper bound, and `uv.lock` resolves
  1.24.0. The core API facts were originally verified against 1.23.0 in specs
  0003 and 0006; Dependabot later raised the declared floor and lock together
  without a client-driven production-code change.
- `huggingface_hub` 1.26.0 fixed CVE-2026-15717 in the `local_dir` download
  mode this project uses. llm-preserver already validates every selected Hub
  filename through `checked_target_path` before the main transfer path calls
  the client, which is an application-layer mitigation; the patched client is
  still the required floor so containment does not depend on that single
  layer or on every future caller remembering to pass through it.
- The existing focused HF tests cover real installed exception, validator,
  and logging symbols, but discovery calls use recording fakes and the primary
  `model_info(files_metadata=True)` / `hf_hub_download(...)` adapter contracts
  lack direct coverage. They prove useful offline behavior, not current live
  connectivity or server compatibility.
- Weekly Dependabot PRs, locked CI, and `pip-audit` remain the package-update
  and advisory mechanisms. The scheduled compatibility workflow fills a
  different gap: a dependency can remain unchanged while the hosted API or
  returned metadata drifts.
- Keep all Hugging Face calls behind the existing `llm_preserver.hub` seam.
  The seam is the intended containment boundary for client churn; this work
  strengthens it rather than spreading client objects through the application.
- The 1.32.0 qualification completed while drafting this spec: the focused HF
  suite passed 49 tests, mypy passed across 89 source files, the full suite
  passed 1,513 tests, and an unauthenticated live comparison against 1.24.0
  returned the same metadata/search shape and downloaded the same pinned
  665-byte `config.json`. These observations inform the target but do not
  replace the test-first implementation and final gate on this branch.
- After this spec ships, each proposed 1.x update reviews the official release
  notes from the locked version through the target and requires the new
  compatibility evidence on its own dependency branch.

## External references

Retrieved 2026-09-21:

- Hugging Face Hub v1.0 migration guide — public-API stability commitment and
  the material HTTP/exception changes at the previous major boundary:
  <https://huggingface.co/docs/huggingface_hub/v1.9.0/en/concepts/migration>
  (Hugging Face documentation; Apache-2.0 repository).
- `huggingface_hub` API reference — `HfApi`, repository metadata/listing, and
  download surfaces whose use is pinned by this spec:
  <https://huggingface.co/docs/huggingface_hub/package_reference/hf_api>
  (Hugging Face documentation; Apache-2.0 repository).
- `huggingface_hub` download guide — `hf_hub_download`, revisions, and
  `local_dir` behavior:
  <https://huggingface.co/docs/huggingface_hub/guides/download>
  (Hugging Face documentation; Apache-2.0 repository).
- `huggingface_hub` 1.26.0 release notes and the corresponding download-path
  hardening change — rejection of absolute, drive-relative, UNC, and
  traversal filenames for `local_dir` and cache paths (CVE-2026-15717):
  <https://github.com/huggingface/huggingface_hub/releases/tag/v1.26.0> and
  <https://github.com/huggingface/huggingface_hub/pull/4540> (Apache-2.0).
- `huggingface_hub` 1.32.0 release notes — the exact client version qualified
  and selected for the development lock:
  <https://github.com/huggingface/huggingface_hub/releases/tag/v1.32.0>
  (Apache-2.0).
- Empirical canary fixture — the public `openai-community/gpt2` Hub repository
  at commit `607a30d783dfa663caf39e06633721c8d4cfcd7e`, whose `config.json`
  measured 665 bytes in unauthenticated 1.24.0 and 1.32.0 live calls on
  2026-09-21:
  <https://huggingface.co/openai-community/gpt2/blob/607a30d783dfa663caf39e06633721c8d4cfcd7e/config.json>
  (model repository license: MIT; observed behavior is empirical).
- Hugging Face Hub utilities reference — client-owned HTTP helpers and the
  announced future transport migration relevant to avoiding raw transport
  coupling:
  <https://github.com/huggingface/huggingface_hub/blob/main/docs/source/en/package_reference/utilities.md>
  (Apache-2.0).
- `huggingface_hub` releases — authoritative change log to inspect across each
  proposed update range:
  <https://github.com/huggingface/huggingface_hub/releases> (Apache-2.0).

## Phase handoff

**As of:** 2026-09-21, after `/plan`
**State:** The 16-file implementation plan is approved, including the direct
`httpx` dependency, 1.26.0 compatibility floor, 1.32.0 lock target, isolated
floor/latest checks, and the pinned public GPT-2 canary above.
**Next phase:** `/test-first`
**Entry conditions:** Write failing dependency-policy and real-adapter contract
tests before changing `pyproject.toml`, `uv.lock`, production code, or CI.

**As of:** 2026-09-21, after `/test-first`
**State:** The focused pre-implementation run produced 11 expected failures
covering dependency bounds, lock versions, locked CI, compatibility automation,
and maintenance documentation; 53 focused tests passed and two opt-in live
tests skipped. No production or dependency behavior had changed.
**Next phase:** Implement
**Entry conditions:** Make the approved 16-file change set pass these contracts
without changing archive, CLI, or selection behavior.

**As of:** 2026-09-21, after `/review`, `/review-adversarial`, and `/security`
**State:** `/review-check` passed with 1,528 tests and two opt-in live skips.
The independent general review found no issues. The adversarial review's one
auto-fix strengthened the workflow contract so removing the matrix override or
`--no-sync` fails tests; both mutations were shown red. Security review was
ship-level with two low findings: floor-environment auditing was added and
verified clean, and checkout credentials no longer persist. Its follow-up found
zero remaining issues, treating mutable action tags as the existing repo-wide
convention rather than changing that convention in only this workflow.
**Next phase:** Human merge review in PR #40
**Entry conditions:** Required GitHub checks are green and GitHub reports the
PR mergeable and clean; the human owns the merge decision.
