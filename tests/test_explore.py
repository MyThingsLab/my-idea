from __future__ import annotations

import json
from pathlib import Path

from mythings.engine import EngineRequest, EngineResult, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from myidea.explore import explore, file_idea, keyword_overlaps, list_ideas

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
            return json.dumps([{"name": "my-scraper"}, {"name": "my-things-core"}])
        if argv[0] == "api" and "contents/docs/tools" in argv[1]:
            return "my-dashboard.md\nmy-news.md\nREADME.md\n"
        if argv[:2] == ["issue", "comment"]:
            self.comments.append(argv[argv.index("--body") + 1])
            return ""
        if argv[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/9\n"
        if argv[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class ScriptedEngine:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[EngineRequest] = []

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return EngineResult(text=json.dumps(self.payload))


class AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW)


class DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY)


BRIEF = {
    "restatement": "One page aggregating research feeds.",
    "overlaps": [
        {"tool": "my-scraper", "why": "fetches pages"},
        {"tool": "made-up-tool", "why": "does not exist"},
    ],
    "contract_fit": "deterministic fetch + one render call",
    "risks": ["feed churn"],
    "smallest_slice": "one feed, one static page",
    "verdict": "fold",
    "fold_into": "my-dashboard",
    "questions": ["daily or on-demand?"],
}


def _explore(fake: FakeGh, engine, policy, tmp_path: Path, **kwargs):
    return explore(
        issue=3,
        engine=engine,
        github=GitHub(repo="o/r", runner=fake),
        policy=policy,
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=fake,
        repo="o/r",
        **kwargs,
    )


def test_explore_posts_brief_and_filters_unknown_overlaps(tmp_path: Path) -> None:
    fake = FakeGh()
    engine = ScriptedEngine(BRIEF)
    result = _explore(fake, engine, AllowAll(), tmp_path)

    assert result.posted and result.verdict == "fold"
    assert len(engine.calls) == 1  # exactly one Engine call
    (comment,) = fake.comments
    assert "my-scraper" in comment
    assert "made-up-tool" not in comment  # cite-only-from-grounding
    assert "fold into `my-dashboard`" in comment
    assert "daily or on-demand?" in comment

    entries = list(Ledger(tmp_path / "ledger.jsonl"))
    assert entries[-1].kind == "idea_explored" and entries[-1].outcome == "success"


def test_noop_engine_degrades_to_grounding_only(tmp_path: Path) -> None:
    fake = FakeGh()
    result = _explore(fake, NoopEngine(), AllowAll(), tmp_path, local_only=True)

    assert not result.posted and fake.comments == []
    assert "No judgment engine attached" in result.comment
    assert "my-scraper" in result.comment  # keyword-matched grounding
    assert "Verdict" not in result.comment  # nothing fabricated


def test_policy_deny_blocks_comment_but_records_honestly(tmp_path: Path) -> None:
    fake = FakeGh()
    result = _explore(fake, ScriptedEngine(BRIEF), DenyAll(), tmp_path)

    assert not result.posted and fake.comments == []
    entries = list(Ledger(tmp_path / "ledger.jsonl"))
    assert entries[-1].outcome == "denied"


def test_keyword_overlaps_matches_name_parts() -> None:
    from myidea.explore import Grounding

    grounding = Grounding(
        org_tools=["my-scraper"], designed_tools=["my-dashboard"], sibling_ideas=[]
    )
    overlaps = keyword_overlaps("a scraper dashboard for feeds", grounding)
    assert {o["tool"] for o in overlaps} == {"my-scraper", "my-dashboard"}


def test_file_and_list_ideas(tmp_path: Path) -> None:
    fake = FakeGh()
    github = GitHub(repo="o/r", runner=fake)
    ledger = Ledger(tmp_path / "ledger.jsonl")

    created = file_idea(title="an idea", github=github, policy=AllowAll(), ledger=ledger)
    assert created is not None and created.number == 9
    assert file_idea(title="an idea", github=github, policy=DenyAll(), ledger=ledger) is None

    ideas = list_ideas(github)
    assert [i.number for i in ideas] == [3]
