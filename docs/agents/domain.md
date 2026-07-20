# Domain Docs

This repository uses a single-context documentation layout.

## Before exploring

Read these files when they exist:

- `CONTEXT.md` at the repository root
- `docs/adr/` for architectural decisions related to the area being changed

If they do not exist, proceed without creating them upfront. The domain-modeling flow creates them when terminology or an important decision needs to be recorded.

## File structure

```text
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-example-decision.md
│   └── 0002-example-decision.md
└── src/
```

## Use the glossary vocabulary

When naming a domain concept in an issue, proposal, or test, use the term defined in `CONTEXT.md`. If the needed term is missing or ambiguous, resolve it through domain modeling before spreading a new term.

## Flag ADR conflicts

If a proposed change contradicts an existing ADR, surface that conflict explicitly instead of silently overriding the decision.