---
name: dependency-hygiene
description: Quality check before adding a new dependency to pyproject.toml. Flags abandoned packages, single-maintainer risk, license conflicts, stdlib alternatives, and known advisories. Surfaces the check; the human approves or rejects.
---

# Dependency hygiene

Trigger: an Edit or Write that adds a new entry to `[project] dependencies` or `[tool.uv] dev-dependencies` in `pyproject.toml` — OR a proposal to add one before the edit is made.

## Why this matters

Dependencies are the easiest place for quality to silently erode. Every dep adds: supply-chain risk, license obligations, upgrade friction, and bus-factor exposure to its maintainer set. A lot of common deps would be a single `import` from the standard library if the agent had thought to check.

## Check before adding

For each new dependency, surface answers to the questions below. Don't add the dep until the check is approved.

### 1. Is it maintained?

- Last release date — older than 18 months is a yellow flag, older than 36 months is a red one.
- Open issues / PR backlog as a rough activity signal.
- Verification: `uv pip show <pkg>` for version + homepage; `pip index versions <pkg>` (or check PyPI directly) for release history.

### 2. Single maintainer?

- A package with one maintainer is a bus-factor risk for anything load-bearing.
- Suggest a verification step the human can do: check the maintainers list on PyPI, or `pip show <pkg> | grep -E 'Author|Maintainer'`.
- If single-maintainer and load-bearing: surface as `[DECISION NEEDED]`.

### 3. License compatibility

- What is the project's intended license? What is the dep's?
- Red flags: GPL into a non-GPL project; SSPL / BSL "look but don't use commercially" custom licenses; no license file at all.
- Verification: `uv pip show <pkg> | grep License`.

### 4. Stdlib alternative

For each dep, ask: is there a stdlib module that would cover this use? Common substitutions worth asking about:

| Adding | Stdlib alternative (when applicable) |
|---|---|
| `requests` | `urllib.request` for one-off simple GETs |
| `python-dateutil` | `datetime.fromisoformat` + `zoneinfo` (3.9+) |
| `attrs` | `dataclasses` for most cases |
| `toml` (reader) | `tomllib` (3.11+, read-only) |
| `pathlib2` | `pathlib` (in stdlib since 3.4) |
| Small wrapper packages around a single stdlib feature | usually the stdlib feature directly |

If stdlib would do, recommend skipping the dep.

### 5. Known advisories

- Verification command: `uv run pip-audit`. Lists CVEs across the dependency tree.
- If the proposed version has known advisories, flag with severity and recommend the patched version (or a different package).
- CI runs `pip-audit` on every PR and Dependabot (`.github/dependabot.yml`) opens patch PRs, so this is the *pre-merge* backstop, not the only check. Still run it *before* adding — catching a vulnerable dep here is cheaper than a red PR after it lands.

### 6. Vendoring / forking flags

- Any package that has been deprecated with a "vendor the source" recommendation upstream?
- Any package with a sketchy maintainer history (recent ownership transfers, takeovers)?
- Any package whose source repo is a fork of a more-maintained upstream?

## Output

When this skill fires, produce a short report:

```
## Dependency check: <package-name> <version-or-spec>

- **Last release:** <date> (<X months ago>)
- **Maintainers:** <count> — <note: bus-factor risk if 1>
- **License:** <license> — <compatible | incompatible | unclear>
- **Stdlib alternative:** <name, or "none">
- **Advisories (proposed version):** <none | <CVE list with severity>>
- **Recommendation:** <add | skip — stdlib covers it | skip — abandoned | skip — license conflict | needs human decision>

Verification commands the human can run:
- `uv pip show <pkg>`
- `uv run pip-audit`
- (etc.)
```

If the dep is fine on every axis, the report is short and ends in "add." Don't pad.

## Forbidden

- **Don't auto-add the dep just because the agent decided it needed one.** Surface the check first. The human decides.
- **Don't run `uv add` until the check is approved.**
- **Don't skip the check for "obvious" deps.** The judgment that something is "obvious" is exactly where bus-factor and license bugs sneak in.
- **Don't fetch external data from PyPI or GitHub yourself.** Recommend the verification commands. The human runs them.

## When to skip

- The dep is already in `pyproject.toml` and the diff is a version bump within the same major. (Still worth a quick `pip-audit` recommendation though.)
- The dep is a transitive — only the direct deps need this check.
- The project is a throwaway / one-off script with no upgrade or maintenance horizon. (Be honest about which projects fit this.)
