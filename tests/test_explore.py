from __future__ import annotations

import json
from pathlib import Path

from mythings.engine import EngineRequest, EngineResult, NoopEngine
from mythings.github import GitHub, GitHubError
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from myidea.explore import (
    Grounding,
    SiblingIdea,
    explore,
    file_idea,
    keyword_overlaps,
    list_ideas,
    similar_ideas,
    web_cross_reference,
)

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
            return json.dumps(
                [
                    {"name": "my-scraper", "description": "fetches and cleans web pages"},
                    {"name": "my-things-core", "description": None},
                ]
            )
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


class FakeGhMissingLabel(FakeGh):
    """Simulates a fresh repo where the 'my-idea' label doesn't exist yet."""

    def __init__(self) -> None:
        super().__init__()
        self.label_created = False

    def __call__(self, argv: list[str]) -> str:
        if argv[:2] == ["issue", "edit"] and not self.label_created:
            self.calls.append(argv)
            raise GitHubError("gh issue edit failed (1): label 'my-idea' not found")
        if argv[:2] == ["label", "create"]:
            self.calls.append(argv)
            self.label_created = True
            return ""
        return super().__call__(argv)


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
    kwargs.setdefault("use_web", False)  # no live HTTP unless a test opts in with a fake fetch
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


def test_grounding_is_carried_in_the_prompt_not_just_context(tmp_path: Path) -> None:
    # ClaudeCLIEngine transmits only system+prompt, so the grounding the model
    # must cite from has to be inline in the prompt.
    fake = FakeGh()
    engine = ScriptedEngine(BRIEF)
    _explore(fake, engine, AllowAll(), tmp_path)
    (request,) = engine.calls
    assert "my-scraper" in request.prompt  # fleet tool reached the model
    assert "fetches and cleans web pages" in request.prompt  # ...with its description


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


def test_file_idea_creates_missing_label_and_retries(tmp_path: Path) -> None:
    fake = FakeGhMissingLabel()
    github = GitHub(repo="o/r", runner=fake)
    ledger = Ledger(tmp_path / "ledger.jsonl")

    created = file_idea(
        title="an idea", github=github, policy=AllowAll(), ledger=ledger, runner=fake
    )

    assert created is not None and created.number == 9
    assert fake.label_created
    edit_calls = [c for c in fake.calls if c[:2] == ["issue", "edit"]]
    assert len(edit_calls) == 2  # first attempt failed, retry after label creation succeeded
    label_calls = [c for c in fake.calls if c[:2] == ["label", "create"]]
    assert label_calls == [
        [
            "label",
            "create",
            "my-idea",
            "--description",
            "Rough tool idea awaiting exploration",
            "--color",
            "fbca04",
            "--force",
            "--repo",
            "o/r",
        ]
    ]

    entries = list(Ledger(tmp_path / "ledger.jsonl"))
    assert entries[-1].kind == "idea_filed" and entries[-1].outcome == "success"


def _issue(number: int, title: str, body: str = "") -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/o/r/issues/{number}",
        "labels": [{"name": "my-idea"}],
    }


class FakeGhWithSibling(FakeGh):
    """Issue list carries a second, similar open idea for clustering/merge."""

    SIBLING = _issue(4, "a research feed aggregator dashboard", "watches feeds all day")

    def __call__(self, argv: list[str]) -> str:
        if argv[:2] == ["issue", "list"]:
            return json.dumps([IDEA_ISSUE, self.SIBLING])
        return super().__call__(argv)


def _npm_payload(name: str, description: str) -> bytes:
    return json.dumps(
        {
            "objects": [
                {
                    "package": {
                        "name": name,
                        "description": description,
                        "license": "MIT",
                        "links": {"npm": f"https://www.npmjs.com/package/{name}"},
                        "date": "2025-01-01",
                    },
                    "score": {"detail": {"popularity": 0.8}},
                }
            ]
        }
    ).encode()


def _fake_fetch(url: str, *, data=None, headers=None) -> bytes:
    if "registry.npmjs.org" in url:
        return _npm_payload("feedparser", "parse RSS/Atom feeds")
    raise AssertionError(f"unexpected web fetch: {url}")


def test_keyword_overlaps_matches_tool_description(tmp_path: Path) -> None:
    grounding = Grounding(
        org_tools=["my-scraper"],
        designed_tools=[],
        tool_descriptions={"my-scraper": "fetches and cleans web pages"},
    )
    overlaps = keyword_overlaps("a tool that cleans messy pages", grounding)
    assert overlaps and overlaps[0]["tool"] == "my-scraper"
    assert "cleans" in overlaps[0]["why"]  # matched on description, not the name


def test_web_cross_reference_returns_candidates() -> None:
    from mythings.github import Issue

    idea = Issue(number=3, title="watch research feeds", body="", url="", labels=["my-idea"])
    candidates = web_cross_reference(idea, fetch=_fake_fetch, registries=("npm",))
    assert [c.name for c in candidates] == ["feedparser"]


def test_web_cross_reference_degrades_on_network_error() -> None:
    from mythings.github import Issue

    def boom(url: str, *, data=None, headers=None) -> bytes:
        raise OSError("no network")

    idea = Issue(number=3, title="watch research feeds", body="", url="", labels=["my-idea"])
    assert web_cross_reference(idea, fetch=boom, registries=("npm",)) == []


def test_explore_renders_web_prior_art(tmp_path: Path) -> None:
    fake = FakeGh()
    payload = {**BRIEF, "prior_art": [{"package": "feedparser", "why": "already parses feeds"}]}
    result = _explore(
        fake, ScriptedEngine(payload), AllowAll(), tmp_path,
        use_web=True, fetch=_fake_fetch, registries=("npm",),
    )
    assert "Prior art on the web" in result.comment
    assert "feedparser" in result.comment


def test_local_only_previews_web_but_writes_nothing(tmp_path: Path) -> None:
    fake = FakeGh()
    payload = {**BRIEF, "prior_art": [{"package": "feedparser", "why": "already parses feeds"}]}
    result = _explore(
        fake, ScriptedEngine(payload), AllowAll(), tmp_path,
        local_only=True, use_web=True, fetch=_fake_fetch, registries=("npm",),
    )
    assert not result.posted and fake.comments == []  # no writes under --local-only
    assert "feedparser" in result.comment  # but the web preview still renders


def test_similar_ideas_clusters_on_shared_tokens() -> None:
    from mythings.github import Issue

    idea = Issue(
        number=3, title="a scraper dashboard for research feeds", body="", url="", labels=[]
    )
    siblings = [
        SiblingIdea(4, "a research feed aggregator dashboard", "watches feeds"),
        SiblingIdea(5, "a raytracer for glass", "unrelated"),
    ]
    similar = similar_ideas(idea, siblings)
    assert [s.number for s, _ in similar] == [4]  # only the overlapping one


def test_merge_verdict_files_consolidated_idea(tmp_path: Path) -> None:
    fake = FakeGhWithSibling()
    merge_brief = {
        **BRIEF,
        "verdict": "merge",
        "merge_proposal": {
            "general_tool": "my-feedhub",
            "absorbs": [4],
            "rationale": "One feed hub beats two narrow tools.",
        },
    }
    engine = ScriptedEngine(merge_brief)
    result = _explore(fake, engine, AllowAll(), tmp_path)

    assert len(engine.calls) == 1  # merge is still a single Engine call
    assert result.verdict == "merge"
    assert result.filed_merge == 9  # FakeGh issue-create returns #9
    creates = [c for c in fake.calls if c[:2] == ["issue", "create"]]
    body = creates[0][creates[0].index("--body") + 1]
    assert "#4" in body  # absorbed sibling cross-linked in the consolidated issue
    announce = [c for c in fake.comments if "#9" in c]
    assert announce and "my-feedhub" in announce[0]

    entries = list(Ledger(tmp_path / "ledger.jsonl"))
    kinds = [e.kind for e in entries]
    assert "idea_filed" in kinds and kinds[-1] == "idea_explored"


def test_tool_slug_trims_descriptive_general_tool() -> None:
    from myidea.explore import _tool_slug

    assert _tool_slug("my-agenda — a single personal-time tool: given a backlog") == "my-agenda"
    assert _tool_slug("my-feedhub") == "my-feedhub"
    assert _tool_slug("my-hub: does things") == "my-hub"


def test_merge_proposal_absorbs_only_similar_ideas(tmp_path: Path) -> None:
    fake = FakeGhWithSibling()
    # Model names an issue (#99) that is not in the deterministic similar set.
    merge_brief = {
        **BRIEF,
        "verdict": "merge",
        "merge_proposal": {"general_tool": "my-feedhub", "absorbs": [4, 99], "rationale": "x"},
    }
    result = _explore(fake, ScriptedEngine(merge_brief), AllowAll(), tmp_path)
    creates = [c for c in fake.calls if c[:2] == ["issue", "create"]]
    body = creates[0][creates[0].index("--body") + 1]
    assert "#4" in body and "#99" not in body  # invented cross-link dropped
    assert result.filed_merge == 9
