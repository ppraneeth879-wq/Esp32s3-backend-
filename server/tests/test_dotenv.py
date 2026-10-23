"""Tests for the .env loader."""

from __future__ import annotations

import os
from pathlib import Path

from app.dotenv import load_dotenv, load_env_file, parse_env_file


def test_parse_simple_pairs(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("A=1\nB=two\n", encoding="utf-8")
    values = parse_env_file(path)
    assert values == {"A": "1", "B": "two"}


def test_parse_ignores_comments_and_blank_lines(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# a comment\n\nA=1\n   \n#another\nB=2\n", encoding="utf-8")
    assert parse_env_file(path) == {"A": "1", "B": "2"}


def test_parse_strips_quotes(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text('A="hello world"\nB=\'single\'\n', encoding="utf-8")
    values = parse_env_file(path)
    assert values["A"] == "hello world"
    assert values["B"] == "single"


def test_parse_handles_export_prefix(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("export A=1\n", encoding="utf-8")
    assert parse_env_file(path) == {"A": "1"}


def test_parse_skips_malformed_lines(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("A=1\nthis is not a pair\n=novalue\nB=2\n", encoding="utf-8")
    assert parse_env_file(path) == {"A": "1", "B": "2"}


def test_parse_of_a_missing_file_is_empty(tmp_path: Path):
    assert parse_env_file(tmp_path / "nope.env") == {}


def test_a_real_environment_variable_wins_over_the_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("A=from_file\n", encoding="utf-8")
    monkeypatch.setenv("A", "from_environment")
    applied = load_env_file(path)
    assert applied == {}
    assert os.environ["A"] == "from_environment"


def test_load_applies_values_that_are_not_already_set(tmp_path: Path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LIFELOG_TEST_KEY=hello\n", encoding="utf-8")
    monkeypatch.delenv("LIFELOG_TEST_KEY", raising=False)
    applied = load_env_file(path)
    assert applied == {"LIFELOG_TEST_KEY": "hello"}
    assert os.environ["LIFELOG_TEST_KEY"] == "hello"
    monkeypatch.delenv("LIFELOG_TEST_KEY", raising=False)


def test_override_replaces_an_existing_value(tmp_path: Path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("A=from_file\n", encoding="utf-8")
    monkeypatch.setenv("A", "from_environment")
    load_env_file(path, override=True)
    assert os.environ["A"] == "from_file"


def test_load_dotenv_defaults_to_the_server_env_file(monkeypatch):
    monkeypatch.delenv("LIFELOG_DOTENV_PROBE", raising=False)
    # The repository's server/.env exists, so this must not raise.
    applied = load_dotenv()
    assert isinstance(applied, dict)
