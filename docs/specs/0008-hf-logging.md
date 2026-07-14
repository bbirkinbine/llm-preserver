# 0008 — Hf Logging

**Status:** draft
**Last updated:** 2026-07-13

## Goal

When a download stalls, the tool is silent about why. The Hugging Face
client has two layers: the Python request layer surfaces rate-limit
retries as warnings, but the Xet byte-transfer layer (the "downloading
bytes" progress bar) handles stalls invisibly — a connection that stops
delivering data waits out a 300-second read timeout, then retries up to
5 times with exponential backoff, all logged below its default console
filter. From the terminal, a stalled-then-recovering transfer is
indistinguishable from a dead one, so the natural response is Ctrl-C
(live use, 2026-07-13) even though the client would usually self-heal
within minutes. Add a `--hf-logging` flag to `pull` and `discover`
that surfaces the HF client's own transfer telemetry live — stall
timeouts, retries, rate-limit waits — so the user can watch a frozen
bar explain itself instead of killing it. The flag does everything
internally; the user sets no environment variables.

## Success criteria

- `pull --hf-logging` and `discover --hf-logging` print the HF
  client's transfer-layer events to the terminal as they happen: Xet
  stall/retry/backoff events (Rust `tracing` at info level) and
  request-layer retry and rate-limit messages
  (`huggingface_hub` logger at info). A transfer that stalls and
  recovers shows the stall and the retries; today it shows nothing.
- The flag requires no environment variables from the user. It works
  by setting `RUST_LOG=info` in the tool's own process environment at
  CLI startup — before the Xet runtime initializes, which reads the
  variable exactly once — and raising the `huggingface_hub` logger to
  info. If `RUST_LOG` is already set in the inherited environment
  (including set-but-empty), the flag leaves it alone — a user who set
  their own filter knows better than the flag — and prints one info
  line saying so, naming the inherited value, so a defeated flag never
  reads as a broken one (adjudicated 2026-07-13, from review finding:
  an empty-string `RUST_LOG` would otherwise silence the Xet layer
  with no explanation).
- The flag prints one activation line at startup, from the tool's own
  logger: telemetry is on, and a healthy transfer is silent — lines
  appear on stalls, retries, and rate-limit waits. Silence must not
  read as a broken flag (adjudicated 2026-07-13, live-use request:
  healthy transfers produce zero vendor output at info, so without an
  activation line the user cannot tell "working and healthy" from
  "not working"). When an inherited `RUST_LOG` wins, the notice line
  carries this role for the Xet half instead. One line, tool-authored,
  about the tool's own configuration action — not synthesized
  connection events, which would cross the no-own-telemetry non-goal.
- The 0007 resume hint carries `--hf-logging` when the pull ran with
  it: the flag exists for the stalled-transfer scenario the hint
  serves, so the continue command must not silently drop it
  (adjudicated 2026-07-13). `--verbose` intentionally still does not
  ride — this criterion is scoped to the flag whose purpose is the
  resume scenario itself.
- Without the flag, output is byte-identical to today (the request
  layer's rate-limit warnings still appear by default, as they do
  now).
- The client identity is untouched: no `library_name`,
  `library_version`, or `user_agent` is ever passed — the tool remains
  a default `huggingface_hub` client (adjudicated 2026-07-13: a
  distinct tool identity risks tool-targeted limiting).
- Log level is pinned to info, never debug: `huggingface_hub`'s
  debug/`HF_DEBUG` modes log every request as a cURL equivalent
  (URLs; auth-adjacent), which must not be one flag away in a tool
  whose users paste terminal output into public issues.
- Before merge, one live pull runs with the flag on and the output is
  checked for token material or auth headers (public-repo hygiene);
  the result is recorded in the spec or PR. <!-- assumption: info
  level is expected clean — the check is verification, not a known
  risk -->
- `--hf-logging` composes with `--verbose` and with each other's
  absence: `--verbose` remains this tool's own diagnostics,
  `--hf-logging` is passthrough vendor telemetry (adjudicated
  2026-07-13: not folded into `--verbose`), and either works alone.
- Exit codes and every other behavior are unchanged — the flag only
  adds output.
- `docs/cli.md` documents the flag under `pull` and `discover`,
  including the stall math that motivates it: the byte layer tolerates
  300 seconds of silence and retries up to 5 times before giving up,
  so a frozen bar often self-heals within ~6 minutes — reach for
  `--hf-logging` (or patience) before Ctrl-C, and the 0007 resume
  hint has your back either way. Power-user tuning knobs
  (`HF_XET_CLIENT_READ_TIMEOUT` and friends) are mentioned as
  environment variables the tool reads through to the client, not
  wrapped in flags.

## Non-goals

- No self-identification to the hub: `library_name` / `user_agent`
  stay at the library default (`unknown/None; hf_hub/...`). Revisit
  only if HF publishes guidance requiring identification.
- Not part of `--verbose`, and no debug tier. Debug-level client
  logging (request URLs, cURL equivalents) stays out of reach of any
  flag.
- No parsing, summarizing, or interpreting of the client's log lines
  — the tool passes the vendor's telemetry through verbatim (the
  no-tool-judgment stance; the logs are the client's own words).
- No stall detection, timeout tuning, or retry machinery of our own —
  the client already has all three; this flag only makes them
  visible.
- No flags wrapping the Xet tuning environment variables
  (`HF_XET_CLIENT_READ_TIMEOUT`, concurrency knobs) — documented as
  env passthrough only.

## Notes

- Ordering constraint: the Xet Rust runtime reads `RUST_LOG` once at
  initialization, so the flag must apply it in CLI startup (alongside
  `setup_logging`) before the first hub-client touch — not lazily at
  transfer time.
- The Xet console log destination/filter env vars have a naming
  discrepancy between the xet-core README (`HF_XET_LOG_FILE`) and
  config source (`HF_XET_LOG_DEST`); this spec needs console output
  only, which `RUST_LOG` alone controls, so the discrepancy is noted
  but not load-bearing.
- Rate-limit context (why stalls are usually not rate limiting): HF
  limits are request counts per 5-minute window (downloads/resolvers:
  3,000 anonymous per IP, 5,000 free account, 12,000 PRO), returned
  as HTTP 429 with `RateLimit` headers; the byte stream itself has no
  documented throttling. `huggingface_hub` >= 1.2.0 already waits out
  429s using the header's reset time. Being logged in is HF's own
  top recommendation against limiting; the tool's ambient-auth
  posture (spec 0003) already covers it.

## Live-check record (2026-07-13, pre-merge)

One live pull ran with the flag on (`pull ggml-org/models
--include 'tinyllamas/stories260K.gguf' --hf-logging --yes`, scratch
archive; the file is Xet-backed, verified via file metadata). Result:

- **Hygiene sweep: clean.** The full output was swept with
  `grep -Ei 'x-amz|signature=|expires=|authorization|bearer|hf_[A-Za-z0-9]{20,}|token='`
  — zero hits. No token material, auth headers, or signed-URL query
  strings at info level.
- **Healthy transfers are silent at info.** The flag-on pull showed
  no Xet lines — and a control run with `RUST_LOG=info` exported
  externally (bypassing the flag's code path entirely) on a fresh
  Xet-backed download was equally silent. Info-tier Xet telemetry
  speaks only on stall/retry events, so silence on a healthy pull is
  the expected behavior on both paths, and the flag path is
  observably identical to the external-export path. A true stall
  could not be reproduced locally (a `HF_XET_CLIENT_READ_TIMEOUT=0`
  probe hung the client rather than logging — pathological value,
  killed); the stall-time output remains verified by upstream source
  (the events are `tracing` info in xet-core's retry machinery)
  rather than live observation.
- **Ordering proven without a stall.** `hf_xet` is not in
  `sys.modules` after importing the CLI package (verified live) —
  `huggingface_hub` imports it lazily inside the download path. So
  whether the Rust side reads `RUST_LOG` at module import or at
  first-transfer runtime init, both happen strictly after the command
  callback writes it. A tripwire test pins the lazy import (an eager
  `import hf_xet` anywhere in startup would silently kill the flag).
  Probing notes: `tracing` tolerates an invalid `RUST_LOG` silently
  (no parse complaint at any point), and an invalid value kills
  `uv run` itself before Python starts (`error: Invalid RUST_LOG
  directives` is uv's — uv is a Rust binary reading the same
  variable; an installed `llm-preserver` entry point is unaffected).

## External references

Facts above that depend on external authority (stall timeout, retry
counts, log-level defaults, rate-limit tiers, `RUST_LOG` semantics)
were fetched 2026-07-13 from the projects' official sources (the
huggingface.co docs pages timed out; content retrieved from the
repositories that generate them, all Apache-2.0):

- Rate limits: https://huggingface.co/docs/hub/rate-limits (via
  https://raw.githubusercontent.com/huggingface/hub-docs/main/docs/hub/rate-limits.md)
- Request-layer retry/backoff and logging:
  https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/utils/_http.py
  and
  https://raw.githubusercontent.com/huggingface/huggingface_hub/main/docs/source/en/package_reference/environment_variables.md
- Xet byte-layer stall/retry defaults and logging:
  https://raw.githubusercontent.com/huggingface/xet-core/main/xet_runtime/src/config/groups/client.rs
  (read_timeout 300s, retry_max_attempts 5, retry_base_delay 3s),
  .../groups/log.rs and .../logging/constants.rs (console default
  `warn`), and the xet-core README.

Implementation must re-verify any of these values it hard-codes (e.g.
in docs text) against the pinned sources per
`.claude/rules/python-code.md` → External-reference provenance.
