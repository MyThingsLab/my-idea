from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from mythings.engine import Engine, EngineRequest
from mythings.github import GitHub, Issue
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

IDEA_LABEL = "my-idea"
ORG = "MyThingsLab"
CORE_REPO = "my-things-core"
MAX_GROUNDING = 50

Runner = Callable[[list[str]], str]

_SYSTEM = (
    "You explore a rough tool idea against an existing fleet of tools. Reply "
    "with only a JSON object of the shape "
    '{"restatement": str, "overlaps": [{"tool": str, "why": str}, ...], '
    '"contract_fit": str, "risks": [str, ...], "smallest_slice": str, '
    '"verdict": "build"|"park"|"fold", "fold_into": str|null, '
    '"questions": [str, ...]}. '
    "An overlap may only name a tool that appears in the provided grounding "
    "lists. No prose, no markdown fences — JSON only."
)


def _gh(argv: list[str]) -> str:
    proc = subprocess.run(["gh", *argv], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass(frozen=True)
class Grounding:
    org_tools: list[str]
    designed_tools: list[str]
    sibling_ideas: list[str]


@dataclass(frozen=True)
class Brief:
    restatement: str
    overlaps: list[dict[str, str]] = field(default_factory=list)
    contract_fit: str = ""
    risks: list[str] = field(default_factory=list)
    smallest_slice: str = ""
    verdict: str = ""
    fold_into: str | None = None
    questions: list[str] = field(default_factory=list)
    deterministic_only: bool = False


@dataclass(frozen=True)
class ExploreResult:
    idea_issue: int
    verdict: str
    posted: bool
    comment: str


def _find_idea_issue(github: GitHub, number: int) -> Issue:
    for issue in github.list_issues(labels=[IDEA_LABEL], state="all", limit=100):
        if issue.number == number:
            return issue
    raise ValueError(f"issue #{number} not found under the '{IDEA_LABEL}' label")


def gather_grounding(github: GitHub, *, runner: Runner = _gh, org: str = ORG) -> Grounding:
    repos = json.loads(runner(["repo", "list", org, "--limit", "200", "--json", "name"]))
    org_tools = sorted(r["name"] for r in repos)[:MAX_GROUNDING]
    raw = runner(["api", f"repos/{org}/{CORE_REPO}/contents/docs/tools", "--jq", ".[].name"])
    designed = sorted(
        name.removesuffix(".md")
        for name in raw.split()
        if name.endswith(".md") and name.startswith("my-")
    )[:MAX_GROUNDING]
    siblings = [
        f"#{i.number} {i.title}"
        for i in github.list_issues(labels=[IDEA_LABEL], state="open", limit=MAX_GROUNDING)
    ]
    return Grounding(org_tools=org_tools, designed_tools=designed, sibling_ideas=siblings)


def keyword_overlaps(idea_text: str, grounding: Grounding) -> list[dict[str, str]]:
    words = {w for w in re.findall(r"[a-z]{4,}", idea_text.lower())}
    overlaps: list[dict[str, str]] = []
    for tool in sorted({*grounding.org_tools, *grounding.designed_tools}):
        parts = {p for p in tool.lower().split("-") if len(p) >= 4}
        hits = words & parts
        if hits:
            overlaps.append({"tool": tool, "why": f"name shares: {', '.join(sorted(hits))}"})
    return overlaps


def _known_tools(grounding: Grounding) -> set[str]:
    return {*grounding.org_tools, *grounding.designed_tools}


def _propose_brief(engine: Engine, idea: Issue, grounding: Grounding) -> Brief:
    prompt = f"Idea issue #{idea.number}: {idea.title}\n\n{idea.body}"
    context = {
        "idea_issue": idea.number,
        "org_tools": grounding.org_tools,
        "designed_tools": grounding.designed_tools,
        "sibling_ideas": grounding.sibling_ideas,
    }
    result = engine.run(EngineRequest(prompt=prompt, system=_SYSTEM, context=context))
    try:
        payload = json.loads(result.text) if result.text else {}
    except json.JSONDecodeError:
        payload = {}
    if not payload.get("restatement"):
        # Honest degrade (NoopEngine, or no usable reply): only the
        # deterministic grounding renders — never fabricated judgment.
        return Brief(
            restatement=idea.title,
            overlaps=keyword_overlaps(f"{idea.title} {idea.body}", grounding),
            deterministic_only=True,
        )
    known = _known_tools(grounding)
    return Brief(
        restatement=str(payload["restatement"]),
        # Cite-only-from-grounding: drop any overlap naming an unknown tool.
        overlaps=[
            {"tool": str(o["tool"]), "why": str(o.get("why", ""))}
            for o in payload.get("overlaps", [])
            if str(o.get("tool", "")) in known
        ],
        contract_fit=str(payload.get("contract_fit", "")),
        risks=[str(r) for r in payload.get("risks", [])],
        smallest_slice=str(payload.get("smallest_slice", "")),
        verdict=str(payload.get("verdict", "")),
        fold_into=payload.get("fold_into"),
        questions=[str(q) for q in payload.get("questions", [])],
    )


def render_brief(brief: Brief, grounding: Grounding) -> str:
    lines = ["## Idea exploration", "", f"**Restatement:** {brief.restatement}", ""]
    if brief.deterministic_only:
        lines += [
            "> No judgment engine attached — deterministic grounding only.",
            "",
        ]
    if brief.overlaps:
        lines.append("**Overlaps:**")
        lines += [f"- `{o['tool']}` — {o['why']}" for o in brief.overlaps]
        lines.append("")
    if brief.contract_fit:
        lines += [f"**Contract fit:** {brief.contract_fit}", ""]
    if brief.risks:
        lines.append("**Risks:**")
        lines += [f"- {r}" for r in brief.risks]
        lines.append("")
    if brief.smallest_slice:
        lines += [f"**Smallest buildable slice:** {brief.smallest_slice}", ""]
    if brief.verdict:
        verdict = brief.verdict
        if brief.verdict == "fold" and brief.fold_into:
            verdict += f" into `{brief.fold_into}`"
        lines += [f"**Verdict:** {verdict}", ""]
    if brief.questions:
        lines.append("**Explore next:**")
        lines += [f"- {q}" for q in brief.questions]
        lines.append("")
    if grounding.sibling_ideas:
        lines.append(f"_Sibling ideas open: {len(grounding.sibling_ideas)}_")
    return "\n".join(lines).rstrip() + "\n"


def explore(
    *,
    issue: int,
    engine: Engine,
    github: GitHub,
    policy: Policy,
    ledger: Ledger,
    runner: Runner = _gh,
    repo: str | None = None,
    org: str = ORG,
    local_only: bool = False,
) -> ExploreResult:
    idea = _find_idea_issue(github, issue)
    grounding = gather_grounding(github, runner=runner, org=org)
    brief = _propose_brief(engine, idea, grounding)
    comment = render_brief(brief, grounding)

    posted = False
    if not local_only:
        action = Action(kind="issue-comment", payload={"issue": issue, "verdict": brief.verdict})
        if policy.evaluate(action).under(unattended=True) is Decision.ALLOW:
            argv = ["issue", "comment", str(issue), "--body", comment]
            if repo:
                argv += ["--repo", repo]
            runner(argv)
            posted = True

    ledger.record(
        "myidea",
        "idea_explored",
        "success" if (posted or local_only) else "denied",
        detail=f"explored #{issue}: {brief.verdict or 'deterministic-only'}",
        idea_issue=issue,
        verdict=brief.verdict,
        fold_into=brief.fold_into,
        posted=posted,
    )
    return ExploreResult(idea_issue=issue, verdict=brief.verdict, posted=posted, comment=comment)


def file_idea(
    *,
    title: str,
    github: GitHub,
    policy: Policy,
    ledger: Ledger,
    body: str = "",
) -> Issue | None:
    action = Action(kind="issue-create", payload={"title": title, "label": IDEA_LABEL})
    if policy.evaluate(action).under(unattended=True) is not Decision.ALLOW:
        return None
    created = github.create_issue(title=title, body=body or "(filed via myidea new)")
    github.add_labels(created.number, [IDEA_LABEL])
    ledger.record(
        "myidea",
        "idea_filed",
        "success",
        detail=f"filed idea #{created.number}: {title}",
        idea_issue=created.number,
    )
    return created


def list_ideas(github: GitHub) -> list[Issue]:
    return github.list_issues(labels=[IDEA_LABEL], state="open", limit=100)
