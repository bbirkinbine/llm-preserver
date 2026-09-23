import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
HOOK = PROJECT_ROOT / ".claude" / "hooks" / "specs-status.sh"


def _write_spec(specs_dir: Path, number: str, title: str, status: str) -> None:
    (specs_dir / f"{number}-{title.lower()}.md").write_text(
        f"# {number} — {title}\n\n**Status:** {status}\n",
        encoding="utf-8",
    )


def test_annotated_shipped_status_is_ranked_rendered_and_counted_as_shipped(
    tmp_path: Path,
) -> None:
    """A recorded PR number must not turn ``shipped`` into a new status."""
    hooks_dir = tmp_path / ".claude" / "hooks"
    specs_dir = tmp_path / "docs" / "specs"
    hooks_dir.mkdir(parents=True)
    specs_dir.mkdir(parents=True)
    script = hooks_dir / "specs-status.sh"
    shutil.copy2(HOOK, script)
    readme = specs_dir / "README.md"
    readme.write_text(
        "# Specs\n\n<!-- specs-status:start -->\nold\n<!-- specs-status:end -->\n",
        encoding="utf-8",
    )
    _write_spec(specs_dir, "0001", "Plain", "shipped")
    _write_spec(specs_dir, "0002", "Annotated", "shipped (PR #7)")
    _write_spec(specs_dir, "0003", "Draft", "draft")
    _write_spec(specs_dir, "0004", "Paused", "paused")

    result = subprocess.run(  # noqa: S603 - the copied repository hook is trusted
        ["/bin/bash", str(script), "--print"],
        check=True,
        capture_output=True,
        text=True,
    )

    plain = "- ~~[0001 — Plain](0001-plain.md)~~  (shipped)"
    annotated = "- ~~[0002 — Annotated](0002-annotated.md)~~  (shipped (PR #7))"
    paused = "- [0004 — Paused](0004-paused.md)  (paused)"
    assert plain in result.stdout
    assert annotated in result.stdout
    assert result.stdout.index(annotated) < result.stdout.index(paused)
    assert result.stdout.rstrip().endswith("1 draft · 2 shipped · 1 paused")
    assert "shipped (PR #7)" not in result.stdout.rsplit("\n", 1)[-1]
    rendered = readme.read_text(encoding="utf-8")
    assert plain in rendered
    assert annotated in rendered
