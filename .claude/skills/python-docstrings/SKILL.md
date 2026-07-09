---
name: python-docstrings
description: Enforces Google-style docstrings on Python public symbols (functions, classes, modules). Auto-invokes when a diff adds a new public symbol, or modifies an existing public symbol whose docstring is missing, tautological, or no longer matches the code. Skips private symbols, trivial helpers, and pure renames.
---

# Python docstring quality

Trigger: writing or editing Python code that adds a new public function, class, method, or module-level symbol — or modifies an existing public symbol that has a missing / tautological / outdated docstring.

## What "public" means here

- Symbols not starting with `_`
- Symbols listed in `__all__`
- Anything re-exported from `__init__.py`

Tests, private helpers (`_underscored`), and standard dunders (`__init__`, `__repr__`, etc.) are exempt from this skill unless they're doing something non-obvious that future readers won't understand from the name.

## Rules

### Functions and methods

- **Trivial helpers** (one expression, no branching, < 5 lines, name is self-describing): one-line summary in imperative voice is enough. Skip the rest.
- **Public functions** (anything else): full Google-style docstring with the sections that apply.

Google-style template:

```python
def function(arg1: type, arg2: type) -> ReturnType:
    """One-line summary in imperative voice — no period at the end of the summary line.

    Longer description when the one-liner is not enough. Wrap at ~88 chars.

    Args:
        arg1: What this is. What constraints apply.
        arg2: What this is. What constraints apply.

    Returns:
        What the return value represents. Omit this section if return type is `None`.

    Raises:
        SpecificException: When this condition occurs.
        OtherException: When this other condition occurs.
    """
```

### Classes

```python
class Thing:
    """One-line summary of what this represents.

    Longer description if needed — typically when and why one would
    construct one of these, and any invariants the class maintains.

    Attributes:
        attr: What this holds.
    """
```

### Modules

Top of every module file:

```python
"""One-paragraph summary of what this module is for and what it exports.

If the module has multiple distinct concepts, that is a smell — consider
splitting (see the `python-module-split` skill).
"""
```

## Forbidden

- **Tautological docstrings.** `"""Get the user."""` on `def get_user(): ...` adds no information. Either explain *which* user, *under what conditions*, *with what side effects* — or omit the docstring if the function is trivial.
- **Docstrings that restate the type signature.** Type hints already say "returns a `User`." Say *which* user.
- **`Raises:` without specifics.** "Raises an exception" is not a `Raises:` section. Name the exception class.
- **Outdated docstrings.** If a docstring describes behavior that no longer matches the code, update it in the same change. Stale docs lie.

## Procedure when this skill fires

1. Identify the new or modified public symbol(s) in the diff.
2. For each one, check the docstring against the rules above.
3. If the docstring is missing, tautological, or stale: write a compliant one, drawing from the spec and the function's actual behavior.
4. Surface the change clearly in the diff (don't bury it).

## Verification

After applying:

```bash
uv run ruff check --select D src/   # pydocstyle rules; checks presence and basic shape
```

Note: ruff's `D` rules check for presence and surface-level shape, not content quality. This skill enforces the quality bar that lint cannot.

## Anti-patterns this skill catches

- `"""Helper function."""` (says nothing)
- `"""Returns a user."""` (the type hint already says that)
- `"""Raises an error if invalid."""` (which error? which condition?)
- Public function with no docstring at all
- Docstring written before the function was renamed, now mismatched

## When to skip

- The symbol is private (`_foo`)
- The function is trivial AND its name fully describes its behavior (`def is_empty(xs): return len(xs) == 0`)
- The change is purely a rename / move with no behavioral change (the old docstring carries over, untouched)
