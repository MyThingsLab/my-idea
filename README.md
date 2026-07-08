# my-idea

[![CI](https://github.com/MyThingsLab/my-idea/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-idea/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-idea/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-idea) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A [MyThingsLab](https://github.com/MyThingsLab/my-things-core) tool that turns
a rough idea into an **explored** idea. File a one-liner as a GitHub issue
labeled `my-idea` (or let the CLI do it), then `myidea explore` grounds it
against what the fleet already has — org repos, the design-plan index, sibling
ideas — makes exactly one Engine call, and posts a structured brief back on
the issue: restatement, overlaps, contract fit, risks, the smallest buildable
slice, a verdict (**build / park / fold**), and probing questions to explore
next.

Design plan: [`my-things-core/docs/tools/my-idea.md`](https://github.com/MyThingsLab/my-things-core/blob/main/docs/tools/my-idea.md)
(historical at first ship).

## Usage

```bash
myidea new "a tool that turns voice memos into backlog issues"
myidea list
myidea explore --issue 3 --engine claude-cli
myidea explore --issue 3 --engine noop --local-only   # deterministic, no side effects
```

- `new` files a `my-idea`-labeled issue (through `Policy`).
- `list` prints the open ideas.
- `explore` posts the brief as an issue comment (through `Policy`);
  `--local-only` prints it instead and touches nothing remote. Against
  `--engine noop` the brief carries only the keyword-matched grounding —
  no fabricated judgment.

Ideas live as issues in this repo by default; point `--repo owner/name`
anywhere else.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ../my-guard -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
