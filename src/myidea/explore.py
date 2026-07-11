from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from mylibrarian.registries import Candidate, Fetcher, _http, retrieve
from mythings.engine import Engine, EngineRequest
from mythings.github import GitHub, GitHubError, Issue
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

IDEA_LABEL = "my-idea"
IDEA_LABEL_DESCRIPTION = "Rough tool idea awaiting exploration"
IDEA_LABEL_COLOR = "fbca04"
ORG = "MyThingsLab"
CORE_REPO = "my-things-core"
MAX_GROUNDING = 50
WEB_REGISTRIES = ("pypi", "npm")
# Two ideas that share at least this many salient tokens are "similar" — the
# seed for a merge/consolidation proposal. Deterministic, no Engine needed.
SIMILAR_MIN_OVERLAP = 2

Runner = Callable[[list[str]], str]

_STOPWORDS = frozenset(
    "that this with from into onto over under about across into your ours "
    "them they what when where which while whose would could should tool "
    "tools thing things idea ideas page pages show shows watch watches".split()
)

_SYSTEM = (
    "You explore a rough tool idea against an existing fleet of tools AND "
    "against prior art on the public web. Reply with only a JSON object of the "
    "shape "
    '{"restatement": str, "overlaps": [{"tool": str, "why": str}, ...], '
    '"prior_art": [{"package": str, "why": str}, ...], '
    '"contract_fit": str, "risks": [str, ...], "smallest_slice": str, '
    '"verdict": "build"|"park"|"fold"|"merge", "fold_into": str|null, '
    '"merge_proposal": {"general_tool": str, "absorbs": [int, ...], '
    '"rationale": str}|null, "questions": [str, ...]}. '
    "An overlap may only name a tool that appears in the provided grounding "
    "lists; a prior_art entry may only name a package present in web_prior_art; "
    "merge_proposal.absorbs may only list issue numbers from similar_ideas. "
    "Use verdict 'merge' only when similar_ideas reveals a cluster that a "
    "single more general tool would serve better than several narrow ones — "
    "then merge_proposal names that general tool and the ideas it absorbs. "
    "No prose, no markdown fences — JSON only."
)


def _gh(argv: list[str]) -> str:
    proc = subprocess.run(["gh", *argv], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass(frozen=True)
class SiblingIdea:
    number: int
    title: str
    body: str = ""


@dataclass(frozen=True)
class Grounding:
    org_tools: list[str]
    designed_tools: list[str]
    sibling_ideas: list[SiblingIdea] = field(default_factory=list)
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    web_candidates: list[Candidate] = field(default_factory=list)


@dataclass(frozen=True)
class MergeProposal:
    general_tool: str
    absorbs: list[int]
    rationale: str


@dataclass(frozen=True)
class Brief:
    restatement: str
    overlaps: list[dict[str, str]] = field(default_factory=list)
    prior_art: list[dict[str, str]] = field(default_factory=list)
    contract_fit: str = ""
    risks: list[str] = field(default_factory=list)
    smallest_slice: str = ""
    verdict: str = ""
    fold_into: str | None = None
    merge_proposal: MergeProposal | None = None
    questions: list[str] = field(default_factory=list)
    deterministic_only: bool = False


@dataclass(frozen=True)
class ExploreResult:
    idea_issue: int
    verdict: str
    posted: bool
    comment: str
    filed_merge: int | None = None


def _find_idea_issue(github: GitHub, number: int) -> Issue:
    for issue in github.list_issues(labels=[IDEA_LABEL], state="all", limit=100):
        if issue.number == number:
            return issue
    raise ValueError(f"issue #{number} not found under the '{IDEA_LABEL}' label")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOPWORDS}


def gather_grounding(
    github: GitHub,
    idea: Issue,
    *,
    runner: Runner = _gh,
    org: str = ORG,
    fetch: Fetcher = _http,
    use_web: bool = True,
    registries: tuple[str, ...] = WEB_REGISTRIES,
) -> Grounding:
    repos = json.loads(
        runner(["repo", "list", org, "--limit", "200", "--json", "name,description"])
    )
    org_tools = sorted(r["name"] for r in repos)[:MAX_GROUNDING]
    descriptions = {
        r["name"]: (r.get("description") or "").strip()
        for r in repos
        if (r.get("description") or "").strip()
    }
    raw = runner(["api", f"repos/{org}/{CORE_REPO}/contents/docs/tools", "--jq", ".[].name"])
    designed = sorted(
        name.removesuffix(".md")
        for name in raw.split()
        if name.endswith(".md") and name.startswith("my-")
    )[:MAX_GROUNDING]
    siblings = [
        SiblingIdea(number=i.number, title=i.title, body=i.body)
        for i in github.list_issues(labels=[IDEA_LABEL], state="open", limit=MAX_GROUNDING)
        if i.number != idea.number
    ]
    web_candidates = (
        web_cross_reference(idea, fetch=fetch, registries=registries) if use_web else []
    )
    return Grounding(
        org_tools=org_tools,
        designed_tools=designed,
        sibling_ideas=siblings,
        tool_descriptions=descriptions,
        web_candidates=web_candidates,
    )


def web_cross_reference(
    idea: Issue,
    *,
    fetch: Fetcher = _http,
    registries: tuple[str, ...] = WEB_REGISTRIES,
    top: int = 8,
) -> list[Candidate]:
    # Best-effort: prior art on the public web is a signal, not a gate. A network
    # or registry hiccup degrades to fleet-only cross-reference rather than
    # failing the run (offline/CI/--local-only), same spirit as NoopEngine.
    try:
        return retrieve(idea.title, idea.body, registries=registries, top=top, fetch=fetch)
    except Exception:
        return []


def keyword_overlaps(idea_text: str, grounding: Grounding) -> list[dict[str, str]]:
    words = _tokens(idea_text)
    overlaps: list[dict[str, str]] = []
    for tool in sorted({*grounding.org_tools, *grounding.designed_tools}):
        name_parts = {p for p in tool.lower().split("-") if len(p) >= 4}
        name_hits = words & name_parts
        desc_hits = (words & _tokens(grounding.tool_descriptions.get(tool, ""))) - name_hits
        if not (name_hits or desc_hits):
            continue
        reasons: list[str] = []
        if name_hits:
            reasons.append(f"name shares: {', '.join(sorted(name_hits))}")
        if desc_hits:
            reasons.append(f"does: {', '.join(sorted(desc_hits)[:5])}")
        overlaps.append({"tool": tool, "why": "; ".join(reasons)})
    return overlaps


def similar_ideas(
    idea: Issue, siblings: list[SiblingIdea], *, min_overlap: int = SIMILAR_MIN_OVERLAP
) -> list[tuple[SiblingIdea, list[str]]]:
    idea_tokens = _tokens(f"{idea.title} {idea.body}")
    scored: list[tuple[SiblingIdea, list[str]]] = []
    for sibling in siblings:
        shared = idea_tokens & _tokens(f"{sibling.title} {sibling.body}")
        if len(shared) >= min_overlap:
            scored.append((sibling, sorted(shared)))
    scored.sort(key=lambda pair: (-len(pair[1]), pair[0].number))
    return scored


def _known_tools(grounding: Grounding) -> set[str]:
    return {*grounding.org_tools, *grounding.designed_tools}


def _parse_merge_proposal(payload: dict, similar_numbers: set[int]) -> MergeProposal | None:
    raw = payload.get("merge_proposal")
    if not isinstance(raw, dict):
        return None
    general = str(raw.get("general_tool", "")).strip()
    if not general:
        return None
    absorbs = [n for n in raw.get("absorbs", []) if isinstance(n, int) and n in similar_numbers]
    return MergeProposal(
        general_tool=general, absorbs=absorbs, rationale=str(raw.get("rationale", ""))
    )


def _grounded_prompt(
    idea: Issue, grounding: Grounding, similar: list[tuple[SiblingIdea, list[str]]]
) -> str:
    lines = [f"Idea issue #{idea.number}: {idea.title}", "", idea.body or "(no description)", ""]

    lines.append("Fleet tools (org repos — cite overlaps only from these):")
    for tool in grounding.org_tools:
        desc = grounding.tool_descriptions.get(tool, "")
        lines.append(f"- {tool}" + (f": {desc}" if desc else ""))
    if grounding.designed_tools:
        lines.append("")
        lines.append("Designed-but-unbuilt tools (also citable):")
        lines += [f"- {t}" for t in grounding.designed_tools]

    lines.append("")
    if grounding.web_candidates:
        lines.append("Prior art on the web (cite prior_art only from these):")
        for c in grounding.web_candidates:
            rel = f", last release {c.last_release}" if c.last_release else ""
            lines.append(f"- {c.name} ({c.registry}, license={c.license}{rel}): {c.description}")
    else:
        lines.append("Prior art on the web: none discovered.")

    lines.append("")
    if similar:
        lines.append("Similar open ideas (merge_proposal.absorbs may only use these numbers):")
        for sibling, shared in similar:
            lines.append(f"- #{sibling.number} {sibling.title} — shares: {', '.join(shared)}")
    else:
        lines.append("Similar open ideas: none — do not propose a merge.")

    return "\n".join(lines)


def _propose_brief(
    engine: Engine,
    idea: Issue,
    grounding: Grounding,
    similar: list[tuple[SiblingIdea, list[str]]],
) -> Brief:
    # The grounding goes in the PROMPT, not just context: ClaudeCLIEngine
    # transmits only system+prompt to the model, so anything the model must
    # cross-reference and cite from has to be inline here (same discipline as
    # my-librarian's candidate list). context stays for the cache key / echo.
    prompt = _grounded_prompt(idea, grounding, similar)
    context = {
        "idea_issue": idea.number,
        "org_tools": grounding.org_tools,
        "tool_descriptions": grounding.tool_descriptions,
        "designed_tools": grounding.designed_tools,
        "sibling_ideas": [{"number": s.number, "title": s.title} for s in grounding.sibling_ideas],
        "similar_ideas": [
            {"number": s.number, "title": s.title, "shared": shared} for s, shared in similar
        ],
        "web_prior_art": [
            {"name": c.name, "registry": c.registry, "description": c.description, "url": c.url}
            for c in grounding.web_candidates
        ],
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
    web_names = {c.name for c in grounding.web_candidates}
    similar_numbers = {s.number for s, _ in similar}
    return Brief(
        restatement=str(payload["restatement"]),
        # Cite-only-from-grounding: drop any overlap naming an unknown tool.
        overlaps=[
            {"tool": str(o["tool"]), "why": str(o.get("why", ""))}
            for o in payload.get("overlaps", [])
            if str(o.get("tool", "")) in known
        ],
        prior_art=[
            {"package": str(p["package"]), "why": str(p.get("why", ""))}
            for p in payload.get("prior_art", [])
            if str(p.get("package", "")) in web_names
        ],
        contract_fit=str(payload.get("contract_fit", "")),
        risks=[str(r) for r in payload.get("risks", [])],
        smallest_slice=str(payload.get("smallest_slice", "")),
        verdict=str(payload.get("verdict", "")),
        fold_into=payload.get("fold_into"),
        merge_proposal=_parse_merge_proposal(payload, similar_numbers),
        questions=[str(q) for q in payload.get("questions", [])],
    )


def render_brief(
    brief: Brief, grounding: Grounding, similar: list[tuple[SiblingIdea, list[str]]]
) -> str:
    lines = ["## Idea exploration", "", f"**Restatement:** {brief.restatement}", ""]
    if brief.deterministic_only:
        lines += ["> No judgment engine attached — deterministic grounding only.", ""]
    if brief.overlaps:
        lines.append("**Overlaps in the fleet:**")
        lines += [f"- `{o['tool']}` — {o['why']}" for o in brief.overlaps]
        lines.append("")
    if brief.prior_art or (brief.deterministic_only and grounding.web_candidates):
        lines.append("**Prior art on the web:**")
        if brief.prior_art:
            lines += [f"- `{p['package']}` — {p['why']}" for p in brief.prior_art]
        else:
            lines += [
                f"- `{c.name}` [{c.registry}]({c.url}): {c.description}"
                for c in grounding.web_candidates
            ]
        lines.append("")
    if brief.deterministic_only and similar:
        lines.append("**Similar open ideas:**")
        lines += [
            f"- #{s.number} {s.title} — shares: {', '.join(shared)}" for s, shared in similar
        ]
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
    if brief.merge_proposal:
        mp = brief.merge_proposal
        absorbs = ", ".join(f"#{n}" for n in mp.absorbs) or "none named"
        lines += [
            f"**Consolidation:** merge into a more general tool `{mp.general_tool}`"
            f" (absorbs {absorbs}).",
        ]
        if mp.rationale:
            lines.append(f"> {mp.rationale}")
        lines.append("")
    if brief.questions:
        lines.append("**Explore next:**")
        lines += [f"- {q}" for q in brief.questions]
        lines.append("")
    if grounding.sibling_ideas:
        lines.append(f"_Sibling ideas open: {len(grounding.sibling_ideas)}_")
    return "\n".join(lines).rstrip() + "\n"


def _merge_body(idea: Issue, mp: MergeProposal) -> str:
    absorbed = "\n".join(f"- #{n}" for n in mp.absorbs) or "- (none named)"
    return (
        f"Consolidated tool proposed by `myidea explore` from idea #{idea.number}.\n\n"
        f"{mp.rationale}\n\n"
        f"**Absorbs these open ideas:**\n{absorbed}\n\n"
        f"_A more general tool than any of the above alone._"
    )


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
    use_web: bool = True,
    fetch: Fetcher = _http,
    registries: tuple[str, ...] = WEB_REGISTRIES,
) -> ExploreResult:
    idea = _find_idea_issue(github, issue)
    # Web retrieval is read-only, so it stays on under --local-only: the preview
    # shows the full brief (fleet + web + merge). --no-web is the opt-out; writes
    # are what --local-only suppresses, below.
    grounding = gather_grounding(
        github,
        idea,
        runner=runner,
        org=org,
        fetch=fetch,
        use_web=use_web,
        registries=registries,
    )
    similar = similar_ideas(idea, grounding.sibling_ideas)
    brief = _propose_brief(engine, idea, grounding, similar)
    comment = render_brief(brief, grounding, similar)

    posted = False
    filed_merge: int | None = None
    if not local_only:
        action = Action(kind="issue-comment", payload={"issue": issue, "verdict": brief.verdict})
        if policy.evaluate(action).under(unattended=True) is Decision.ALLOW:
            _comment(runner, issue, comment, repo)
            posted = True
        if brief.merge_proposal is not None:
            filed_merge = _file_merge(idea, brief.merge_proposal, github, policy, ledger)
            if filed_merge is not None and posted:
                _comment(
                    runner,
                    issue,
                    f"🔀 Filed consolidated idea #{filed_merge}: "
                    f"`{brief.merge_proposal.general_tool}`.",
                    repo,
                )

    ledger.record(
        "myidea",
        "idea_explored",
        "success" if (posted or local_only) else "denied",
        detail=f"explored #{issue}: {brief.verdict or 'deterministic-only'}",
        idea_issue=issue,
        verdict=brief.verdict,
        fold_into=brief.fold_into,
        posted=posted,
        filed_merge=filed_merge,
    )
    return ExploreResult(
        idea_issue=issue,
        verdict=brief.verdict,
        posted=posted,
        comment=comment,
        filed_merge=filed_merge,
    )


def _comment(runner: Runner, issue: int, body: str, repo: str | None) -> None:
    argv = ["issue", "comment", str(issue), "--body", body]
    if repo:
        argv += ["--repo", repo]
    runner(argv)


def _file_merge(
    idea: Issue, mp: MergeProposal, github: GitHub, policy: Policy, ledger: Ledger
) -> int | None:
    created = file_idea(
        title=mp.general_tool,
        github=github,
        policy=policy,
        ledger=ledger,
        body=_merge_body(idea, mp),
    )
    return created.number if created is not None else None


def _ensure_idea_label(runner: Runner, repo: str | None) -> None:
    argv = [
        "label",
        "create",
        IDEA_LABEL,
        "--description",
        IDEA_LABEL_DESCRIPTION,
        "--color",
        IDEA_LABEL_COLOR,
        "--force",
    ]
    if repo:
        argv += ["--repo", repo]
    runner(argv)


def file_idea(
    *,
    title: str,
    github: GitHub,
    policy: Policy,
    ledger: Ledger,
    body: str = "",
    runner: Runner = _gh,
) -> Issue | None:
    action = Action(kind="issue-create", payload={"title": title, "label": IDEA_LABEL})
    if policy.evaluate(action).under(unattended=True) is not Decision.ALLOW:
        return None
    created = github.create_issue(title=title, body=body or "(filed via myidea new)")
    try:
        github.add_labels(created.number, [IDEA_LABEL])
    except GitHubError:
        # First idea filed against a fresh repo: the "my-idea" label doesn't
        # exist yet. Create it (idempotent via --force) and retry once.
        _ensure_idea_label(runner, github.repo)
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
