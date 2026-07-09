# my-idea — agent instructions

You are developing **my-idea**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** explores a rough idea (a `my-idea`-labeled issue) against the
  existing fleet and posts a structured exploration brief back on the issue —
  restatement, overlaps, contract fit, risks, smallest buildable slice,
  verdict (build / park / fold), probing questions.
- **The single Engine call:** "explore this idea against this fleet" — grounded
  in deterministically gathered org repos, design-plan titles, and sibling
  ideas; overlaps may only cite tools from that grounding. Against `NoopEngine`
  the brief renders only the keyword-matched grounding, never fabricated
  judgment.
- **Invariants / rules:** comment-only side effects, through `Policy`; never
  files issues or opens PRs on its own (v0); grounding lists are size-capped;
  `--local-only` must touch nothing remote.
- **Backlog label:** `my-idea`
- **Verify:** `myidea explore --issue <n> --engine noop --local-only` (prints
  the deterministic brief, no side effects); `myidea list` for the read-only
  path.
