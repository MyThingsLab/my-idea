# my-idea — agent instructions

You are developing **my-idea**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** explores a rough idea (a `my-idea`-labeled issue) by
  auto-cross-referencing it against the existing fleet **and** prior art on the
  public web (via my-librarian), then posts a structured brief back on the
  issue — restatement, fleet overlaps, web prior art, contract fit, risks,
  smallest buildable slice, verdict (build / park / fold / **merge**), and, when
  several open ideas cluster, a **consolidation** proposal for a single more
  general tool.
- **The single Engine call:** "explore this idea against this fleet and the
  web" — grounded in deterministically gathered org repos **and their
  descriptions**, design-plan titles, sibling ideas (with token-overlap
  similarity clustering), and my-librarian's live PyPI/npm candidates. Overlaps
  may only cite grounding tools; `prior_art` only web candidates;
  `merge_proposal.absorbs` only issue numbers from the detected similar set.
  Against `NoopEngine` the brief renders only the deterministic grounding
  (keyword+description overlaps, similar-idea cluster, web candidates), never
  fabricated judgment.
- **Web cross-reference:** best-effort and read-only — my-librarian's LLM-free
  HTTP retrieval. A network error degrades to fleet-only (never fails the run);
  `--no-web` and `--local-only` skip it entirely (the deterministic Verify
  path). The HTTP boundary is injectable (`fetch`) and mocked in tests.
- **Invariants / rules:** comment-only *except* the `merge` verdict, which may
  file **one** consolidated `my-idea` issue through `Policy` (the absorbed
  siblings are cross-linked by `#N` in its body); still exactly one Engine call
  per run; never opens PRs or edits code; grounding lists are size-capped;
  `--local-only` touches nothing remote (no comment, no filed issue, no web).
- **Backlog label:** `my-idea`
- **Verify:** `myidea explore --issue <n> --engine noop --local-only` (prints
  the deterministic brief, no side effects, no network); `myidea list` for the
  read-only path.
