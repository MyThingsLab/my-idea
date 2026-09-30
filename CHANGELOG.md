# Changelog

## [Unreleased]
### Added/Changed
- explore/new/list: grounding (org repos + design index + sibling ideas), one Engine call, cite-only-from-grounding overlap filter, honest NoopEngine degrade, Policy-gated comment
- Mechanical migration to mythings.testing: the payload-JSON engine spy became a scripted() factory over the shared ScriptedEngine; the stateful domain gh doubles stay local (label-creation/topic-dispatch shapes the shared FakeGh doesn't model).
### Fixed
- file_idea() now creates the my-idea label (idempotent, --force) and retries once when add_labels fails on a fresh repo that has no my-idea label yet
