from __future__ import annotations

import json
from pathlib import Path

import pytest
from mythings.github import GitHub
from mythings.policy import Decision, PolicyResult

from myidea import cli
from myidea.explore import ExploreResult

IDEA_ISSUE = {
    "number": 3,
    "title": "a scraper dashboard for research feeds",
    "body": "Something that watches feeds and shows them on one page.",
    "url": "https://github.com/o/r/issues/3",
    "labels": [{"name": "my-idea"}],
}


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.comments: list[str] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["issue", "list"]:
            return json.dumps([IDEA_ISSUE])
        if argv[:2] == ["repo", "list"]:
            return json.dumps([{"name": "my-scraper", "description": "fetches web pages"}])
        if argv[0] == "api" and "contents/docs/tools" in argv[1]:
            return "my-dashboard.md\nREADME.md\n"
        if argv[:2] == ["issue", "comment"]:
            self.comments.append(argv[argv.index("--body") + 1])
            return ""
        if argv[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/9\n"
        if argv[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class AllowAll:
    def evaluate(self, action):
        return PolicyResult(Decision.ALLOW)


class DenyAll:
    def evaluate(self, action):
        return PolicyResult(Decision.DENY)


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # main() builds Ledger(".mythings/ledger.jsonl") itself with no injection
    # point, so isolate the ledger file per test via cwd instead.
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(cli, "GitHub", lambda repo=None: GitHub(repo=repo, runner=fake))
    # cli.py never threads a `runner=` through to explore()/file_idea(), so
    # they fall back to their `Runner = _gh` default, which is bound at def
    # time -- patching the module attribute doesn't reach it. Patch the true
    # external boundary (subprocess.run) instead so nothing shells out for real.
    def fake_run(argv, **kwargs):
        class Proc:
            returncode = 0
            stdout = fake(list(argv[1:]))
            stderr = ""

        return Proc()

    monkeypatch.setattr("myidea.explore.subprocess.run", fake_run)
    return fake


def test_new_files_an_idea(fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["new", "a title", "--body", "some body"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "filed idea #9: a title" in out
    create_call = next(c for c in fake_github.calls if c[:2] == ["issue", "create"])
    assert "some body" in create_call


def test_new_denied_by_policy(fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "Guard", DenyAll)
    rc = cli.main(["new", "a title"])
    assert rc == 1
    assert "filing denied by policy" in capsys.readouterr().out


def test_list_prints_open_ideas(
    fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["list"])
    assert rc == 0
    assert "#3 a scraper dashboard for research feeds" in capsys.readouterr().out


def test_list_prints_message_when_empty(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    fake = FakeGh()

    def empty_list(argv: list[str]) -> str:
        if argv[:2] == ["issue", "list"]:
            return json.dumps([])
        return fake(argv)

    monkeypatch.setattr(cli, "GitHub", lambda repo=None: GitHub(repo=repo, runner=empty_list))
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["list"])
    assert rc == 0
    assert "no open ideas" in capsys.readouterr().out


def test_explore_local_only_prints_comment(
    fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["explore", "--issue", "3", "--local-only", "--no-web"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Idea exploration" in out
    assert fake_github.comments == []  # local-only: no side effects


def test_explore_posts_and_reports_verdict(
    fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["explore", "--issue", "3", "--no-web"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "explored #3: deterministic-only (comment posted)" in out
    assert len(fake_github.comments) == 1


def test_explore_unknown_issue_reports_value_error(
    fake_github: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "Guard", AllowAll)
    rc = cli.main(["explore", "--issue", "404", "--no-web"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "myidea:" in out
    assert "issue #404 not found" in out


def test_explore_engine_choice_is_wired_to_the_named_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: --engine must select the matching Engine class, not always noop.
    used: dict[str, object] = {}

    def fake_explore(*, engine, **kwargs):
        used["engine"] = engine
        return ExploreResult(idea_issue=3, verdict="", posted=False, comment="")

    monkeypatch.setattr(cli, "explore", fake_explore)
    cli.main(["explore", "--issue", "3", "--local-only", "--no-web", "--engine", "claude-cli"])
    assert isinstance(used["engine"], cli._ENGINES["claude-cli"])
