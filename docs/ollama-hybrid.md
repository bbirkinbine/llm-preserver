# Running archived models alongside a normal Ollama install

The hybrid setup: keep your existing Ollama exactly as it is for
daily workhorses, and serve archived models from a view on a second
port when you want them. Nothing about your current Ollama changes —
no login items, no store migration, no reconfigured clients.

This guide assumes the `views` command from
[`cli.md`](cli.md#views--run-archived-models-in-place-phase-1-ollama)
and an archive with at least one usable (GGUF + recorded SHA256)
model.

## Why hybrid

Two ways to run an archived model with Ollama, with opposite
trade-offs:

| | Official import (`ollama create`) | View (`--seed-store`) |
| --- | --- | --- |
| Disk cost | Full copy of the weights in `~/.ollama/models` | Kilobytes (symlinks + manifests) |
| Ollama support | Official | Works, verified — but not an officially supported setup; an Ollama upgrade can break the view (regenerate it; the archive is never at risk) |
| Survives Ollama upgrades | Yes | Usually; regenerate if not |
| Good for | Small always-on models (embedders, rerankers — a few GB) | Large on-demand models (chat/coding models, tens of GB) |

The hybrid uses both where each is strong:

- **Default server (port 11434, store `~/.ollama`).** Your existing
  install, untouched. Small always-loaded models — an embedding model
  a scheduled indexing job hits every half hour, say — live here via
  normal `ollama pull`/`ollama create`. The couple of GB duplicated
  from the archive is a fair price for an official, upgrade-proof
  setup on a model that must always work. Archive the model anyway
  (`pull` its GGUF) so the weights are preserved independently of
  Ollama's store.
- **View server (side port, store = the seeded view).** Archived
  models too large to duplicate — served in place from the archive,
  zero bytes copied, started when you want them and stopped when you
  don't.

Two `ollama serve` instances coexist cleanly as long as each has its
own port (`OLLAMA_HOST`) and its own store (`OLLAMA_MODELS`).

## Working example

One-time setup (add the exports to your shell profile):

```bash
export LLM_PRESERVER_ARCHIVE=~/models
export LLM_PRESERVER_VIEWS=~/llm-views
```

Seed (or refresh) the view — run again whenever the archive gains or
loses models:

```bash
llm-preserver views --seed-store
# prints the usable models with their run-ready names, e.g.:
#   usable:
#     Qwen/Qwen3.6-27B → qwen/qwen3.6-27b:q8_0
```

Start the view server on a side port (any free port; 11500 in these
examples):

```bash
OLLAMA_MODELS=$LLM_PRESERVER_VIEWS/ollama OLLAMA_NOPRUNE=1 \
  OLLAMA_HOST=127.0.0.1:11500 ollama serve
```

Use it — every client of the view server needs the same
`OLLAMA_HOST`:

```bash
OLLAMA_HOST=127.0.0.1:11500 ollama list
OLLAMA_HOST=127.0.0.1:11500 ollama run qwen/qwen3.6-27b:q8_0
```

Meanwhile, plain `ollama …` commands and every existing client keep
hitting your default server on 11434, which never knows the view
server exists. API clients pick the server the same way — point their
base URL at `http://localhost:11500` for archived models,
`http://localhost:11434` for the default store.

Stop the view server with Ctrl-C (or kill it) whenever; start it
again the same way. The view itself persists between runs — seeding
and serving are independent.

## Things worth knowing

- **Memory is shared even though the servers are not.** Each instance
  loads models into RAM/VRAM independently; a small resident embedder
  plus one large chat model is the normal case, but two servers each
  holding a large model add up. Ollama unloads idle models after a
  few minutes by default, so pressure self-corrects.
- **A forgotten `OLLAMA_HOST` on a client means the wrong server**,
  and the symptom is "model not found" (the default server doesn't
  have the archived names). Confusing once, obvious afterwards.
- **The stores never mix.** Models pulled into `~/.ollama` don't
  appear on the view server and vice versa. The minted archive names
  (`creator/model:tag`) also make it obvious at a glance which server
  a name belongs to.
- **Refresh after archive changes**: `llm-preserver views
  --seed-store` again (seconds, idempotent), then restart the view
  server so it re-reads the store.
- **`OLLAMA_NOPRUNE=1` is required on the view server only.** The
  default server keeps its normal pruning behavior.

## Optional: start the view server at login

If a manual start becomes tiresome, a macOS LaunchAgent runs the view
server at login without touching the default Ollama install. Save as
`~/Library/LaunchAgents/local.ollama-view.plist` (replace `HOME` and
the `ollama` path — `which ollama` — with real absolute paths;
launchd does not expand `~` or variables in these fields):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>local.ollama-view</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_MODELS</key><string>/Users/USERNAME/llm-views/ollama</string>
        <key>OLLAMA_NOPRUNE</key><string>1</string>
        <key>OLLAMA_HOST</key><string>127.0.0.1:11500</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/ollama-view.log</string>
    <key>StandardErrorPath</key><string>/tmp/ollama-view.log</string>
</dict>
</plist>
```

Load it with `launchctl load ~/Library/LaunchAgents/local.ollama-view.plist`.
Because it binds a side port and its own store, it coexists with the
default Ollama app/service indefinitely. On Linux, the equivalent is a
systemd user unit with the same three environment variables.
