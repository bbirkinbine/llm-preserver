"""Tests for the LLM_PRESERVER_ARCHIVE environment-variable fallback.

Every command's archive-path argument may be omitted when the env var
is set; an explicit path always wins; neither set is a usage error.
Spec 0003, scope addition 2026-07-10.
"""

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

ENV_VAR = "LLM_PRESERVER_ARCHIVE"


def init_archive_dir(tmp_path, name="archive"):
    archive = tmp_path / name
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def test_status_falls_back_to_env_var(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["status"], env={ENV_VAR: str(archive)})

    assert result.exit_code == 0


def test_explicit_path_overrides_env_var(tmp_path):
    real = init_archive_dir(tmp_path, "real")
    decoy = tmp_path / "decoy"  # not an archive: env must NOT win

    result = runner.invoke(app, ["status", str(real)], env={ENV_VAR: str(decoy)})

    assert result.exit_code == 0


def test_missing_path_and_env_is_usage_error(tmp_path):
    result = runner.invoke(app, ["status"], env={ENV_VAR: ""})

    assert result.exit_code != 0


def test_init_accepts_env_var_target(tmp_path):
    target = tmp_path / "fresh"
    target.mkdir()

    result = runner.invoke(app, ["init"], env={ENV_VAR: str(target)})

    assert result.exit_code == 0
    assert (target / "archive.json").is_file()


def test_pull_omits_path_when_env_var_set(tmp_path, monkeypatch, fake_hub_factory):
    """The bug Brian hit live: `pull REPO_ID` alone must work with the env var."""
    import llm_preserver.cli as cli_module

    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    monkeypatch.setattr(cli_module, "HubClient", lambda: client)

    result = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", "--include", "*Q4_K_M*", "--model", "acme/tiny-chat"],
        env={ENV_VAR: str(archive)},
    )

    assert result.exit_code == 0
    assert (archive / "models" / "acme" / "tiny-chat").is_dir()


def test_show_omits_path_when_env_var_set(tmp_path, monkeypatch, fake_hub_factory):
    import llm_preserver.cli as cli_module

    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    monkeypatch.setattr(cli_module, "HubClient", lambda: client)
    pulled = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", "--include", "*Q4_K_M*", "--model", "acme/tiny-chat"],
        env={ENV_VAR: str(archive)},
    )
    assert pulled.exit_code == 0

    result = runner.invoke(app, ["show", "acme/tiny-chat"], env={ENV_VAR: str(archive)})

    assert result.exit_code == 0


def test_init_hints_at_env_var_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    target = tmp_path / "archive"
    target.mkdir()

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 0
    assert ENV_VAR in result.output
    assert str(target.resolve()) in result.output


def test_init_prints_no_hint_when_env_var_already_set(tmp_path):
    target = tmp_path / "archive"
    target.mkdir()

    result = runner.invoke(app, ["init", str(target)], env={ENV_VAR: str(target)})

    assert result.exit_code == 0
    assert "hint:" not in result.output
