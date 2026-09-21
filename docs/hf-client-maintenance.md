# Hugging Face client maintenance

`llm-preserver` supports `huggingface_hub>=1.26.0,<2`. The lower bound is a
security floor: 1.26.0 contains the `local_dir` path-containment fix for
CVE-2026-15717. The upper bound treats a future major version as potentially
breaking until this repository deliberately qualifies it.

The exact version in `uv.lock` is the reproducible development and ordinary-CI
version. It may advance within 1.x without raising the declared minimum. Raise
the minimum only for a security fix, a newly used API, or an explicit decision
to end support for older clients.

## Qualify a proposed update

Use a dedicated dependency-update branch. Before changing the lock, read every
official release entry from the currently locked version through the proposed
version at <https://github.com/huggingface/huggingface_hub/releases>. Pay
particular attention to `HfApi`, `ModelInfo`, downloads and `local_dir`, HTTP
transport, exceptions, authentication and tokens, gated repositories, logging,
and Python-version requirements.

Refresh only the relevant packages, then record the exact resolution:

```bash
uv lock --upgrade-package huggingface-hub --upgrade-package anyio
uv sync --locked
uv run python -c 'import huggingface_hub; print(huggingface_hub.__version__)'
```

Run the deterministic gate and advisory scan:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest
uv run --with pip-audit pip-audit
```

Then qualify both supported endpoints without rewriting `uv.lock`:

```bash
uv run --frozen --with 'huggingface-hub==1.26.0' pytest \
  tests/test_hf_client_compatibility.py tests/test_hub.py \
  tests/test_hub_discovery_client.py tests/test_hf_logging.py
uv run --frozen --with 'huggingface-hub<2' pytest \
  tests/test_hf_client_compatibility.py tests/test_hub.py \
  tests/test_hub_discovery_client.py tests/test_hf_logging.py
uv run --frozen --with 'huggingface-hub==1.26.0' --with pip-audit pip-audit
uv run --frozen --with 'huggingface-hub<2' --with pip-audit pip-audit
```

The scheduled workflow is also available through `workflow_dispatch`. It
prints the resolved version for its floor and latest-1.x jobs, runs the offline
adapter contracts, audits each resolved environment, and performs anonymous
live metadata, discovery, and pinned download checks against public
`openai-community/gpt2`. It disables implicit tokens and stores all Hugging
Face state under ephemeral runner storage.

## When deeper live checks are required

The public canary proves the public Hub path only. If an intervening release
mentions authentication, token discovery, gated or private repositories, or
HTTP transport behavior, add a deliberate authenticated verification before
merging. Use a maintainer-controlled test repository and a least-privilege,
short-lived credential; never add that token to the public scheduled workflow
or include it in logs.

Treat an install/import failure, metadata or discovery failure, and download
failure as separate diagnoses. Do not infer live compatibility from a parsed
configuration file or a successful dependency install. Record the tested
version, commands, release-note range, and any follow-up in the update PR.
