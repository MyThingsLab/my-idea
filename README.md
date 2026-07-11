# my-idea

[![CI](https://github.com/MyThingsLab/my-idea/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-idea/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-idea/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-idea) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A [MyThingsLab](https://github.com/MyThingsLab/my-things-core) tool that turns
a rough idea into an **explored** idea. File a one-liner as a GitHub issue
labeled `my-idea` (or let the CLI do it), then `myidea explore`
auto-cross-references it against what already exists — org repos **and their
descriptions**, the design-plan index, sibling ideas, **and prior art on PyPI /
npm** (discovered via [my-librarian](https://github.com/MyThingsLab/my-librarian)) —
makes exactly one Engine call, and posts a structured brief back on the issue:
restatement, fleet overlaps, web prior art, contract fit, risks, the smallest
buildable slice, a verdict (**build / park / fold / merge**), and probing
questions to explore next.

When several open ideas cluster on shared themes, the brief proposes a
**consolidation** — a single more general, more useful tool — and, on the
`merge` verdict, files that consolidated `my-idea` issue (through `Policy`),
cross-linking the ideas it absorbs.

Design plan: [`my-things-core/docs/tools/my-idea.md`](https://github.com/MyThingsLab/my-things-core/blob/main/docs/tools/my-idea.md)
(historical at first ship).

## Usage

```bash
myidea new "a tool that turns voice memos into backlog issues"
myidea list
myidea explore --issue 3 --engine claude-cli
myidea explore --issue 3 --engine claude-cli --no-web   # fleet-only, skip web prior art
myidea explore --issue 3 --engine noop --local-only     # deterministic, no side effects
```

- `new` files a `my-idea`-labeled issue (through `Policy`).
- `list` prints the open ideas.
- `explore` posts the brief as an issue comment (through `Policy`), and on the
  `merge` verdict files one consolidated idea; `--local-only` prints the brief
  instead and touches nothing remote (no comment, no filed issue, no web).
  `--no-web` skips the my-librarian web cross-reference (fleet-only). Against
  `--engine noop` the brief carries only the deterministic grounding —
  fleet/description overlaps, the similar-idea cluster, and web candidates —
  with no fabricated judgment.

Ideas live as issues in this repo by default; point `--repo owner/name`
anywhere else.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ../my-guard -e ../my-librarian -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
