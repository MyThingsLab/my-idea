from __future__ import annotations

import argparse
from pathlib import Path

from myguard import Guard
from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger

from myidea.explore import explore, file_idea, list_ideas

_ENGINES: dict[str, type[Engine]] = {"noop": NoopEngine, "claude-cli": ClaudeCLIEngine}
_LEDGER_PATH = Path(".mythings") / "ledger.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myidea")
    sub = parser.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="file an idea as a my-idea-labeled issue")
    new.add_argument("title")
    new.add_argument("--body", default="")
    new.add_argument("--repo", default=None, help="owner/name; defaults to the current repo")

    ls = sub.add_parser("list", help="list open ideas")
    ls.add_argument("--repo", default=None)

    ex = sub.add_parser("explore", help="explore one idea and post the brief on its issue")
    ex.add_argument("--issue", type=int, required=True)
    ex.add_argument("--repo", default=None)
    ex.add_argument("--engine", choices=sorted(_ENGINES), default="noop")
    ex.add_argument("--local-only", action="store_true", help="print the brief; no side effects")
    ex.add_argument(
        "--no-web",
        action="store_true",
        help="skip my-librarian web prior-art cross-reference (fleet-only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    github = GitHub(repo=args.repo)
    ledger = Ledger(_LEDGER_PATH)

    try:
        if args.cmd == "new":
            created = file_idea(
                title=args.title, github=github, policy=Guard(), ledger=ledger, body=args.body
            )
            if created is None:
                print("myidea: filing denied by policy")
                return 1
            print(f"filed idea #{created.number}: {created.title}")
            return 0

        if args.cmd == "list":
            ideas = list_ideas(github)
            for issue in ideas:
                print(f"#{issue.number} {issue.title}")
            if not ideas:
                print("no open ideas")
            return 0

        result = explore(
            issue=args.issue,
            engine=_ENGINES[args.engine](),
            github=github,
            policy=Guard(),
            ledger=ledger,
            repo=args.repo,
            local_only=args.local_only,
            use_web=not args.no_web,
        )
    except ValueError as exc:
        print(f"myidea: {exc}", flush=True)
        return 1

    if args.local_only:
        print(result.comment)
    else:
        print(
            f"explored #{result.idea_issue}: {result.verdict or 'deterministic-only'}"
            f" ({'comment posted' if result.posted else 'comment NOT posted'})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
