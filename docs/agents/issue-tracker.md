# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top of each issue file
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says “publish to the issue tracker”

Create a new file under `.scratch/<feature-slug>/`, creating the directory if needed.

## When a skill says “fetch the relevant ticket”

Read the referenced Markdown file. The user will normally provide the path or issue number directly.

## Wayfinding operations

Used by `/wayfinder`.

- Map: `.scratch/<effort>/map.md`
- Child tickets: `.scratch/<effort>/issues/NN-<slug>.md`
- Blocking: record dependencies with a `Blocked by: NN, NN` line
- Frontier: use the first open, unblocked, unclaimed ticket by number
- Claim: set `Status: claimed` before work
- Resolve: append the answer, set `Status: resolved`, and update the map