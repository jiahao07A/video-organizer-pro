# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

GitHub Issues are the shared task identity and collaboration log. The detailed execution record remains in `.scratch/`, so each non-trivial task should link the two records. The complete Git/GitHub workflow is defined in [`git-github-workflow.md`](./git-github-workflow.md).

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage on GitHub uses one of the five canonical labels in [`triage-labels.md`](./triage-labels.md)
- Triage state in local Markdown is recorded as a `Status:` line near the top of each issue file
- Comments and conversation history append to the bottom of the file under a `## Comments` heading
- When a GitHub Issue exists, add `GitHub Issue: #N` to the local spec and add the local spec path to the GitHub Issue
- When a branch or PR exists, record `Branch:` and `PR:` in the local spec or the relevant local issue

## When a skill says “publish to the issue tracker”

Create a new file under `.scratch/<feature-slug>/`, creating the directory if needed.

## When a skill says “fetch the relevant ticket”

Read the referenced Markdown file. The user will normally provide the path or issue number directly.

## What belongs where

- **GitHub Issue**：任务身份、共享范围、讨论、方案决定、PR 关联和关闭总结。
- **`.scratch/` spec**：完整目标、范围内/范围外、拆分顺序、依赖、验收矩阵和交接信息。
- **`.scratch/` child issue**：一个可以独立实现和验证的执行单元。

These are linked records, not competing sources of truth. If they disagree, record the latest decision in the GitHub Issue first, then update the local spec or issue before continuing implementation.

## When a local record is required

Create or update a local spec for every feature, bug fix, refactor, performance task, data/file operation, dependency/build change, and workflow/documentation change that is large enough to need a GitHub Issue or more than one commit. A one-line typo or a same-scope formatting correction may be kept in the parent record or PR description.

Bundle small tasks only when they share the same user outcome, acceptance criteria, risk, rollback path and reviewer. Split larger work into child issues when the outcomes, risks, dependencies, reviewers or delivery timing differ.

## Required local fields

The spec or child issue should make the following visible before implementation:

- Goal and background
- Scope in and scope out
- Acceptance criteria
- Dependencies and constraints
- Risk and rollback
- Validation plan
- GitHub Issue, branch and PR links when available

For an implementation ticket, use the existing `Status:` line and record blocking relationships with `Blocked by: NN, NN` when applicable.

## Comment and handoff format

Append material changes under `## Comments`. Prefer explicit headings such as:

- `状态更新`
- `范围决定`
- `方案决定`
- `验证记录`
- `问题`
- `交接`

A handoff should include the current branch, last commit, completed checks, remaining work, known risks and the next action. Do not silently replace earlier decisions. When work is complete, record the PR, merge commit, validation result and unresolved limitations before marking the local record resolved.

## Wayfinding operations

Used by `/wayfinder`.

- Map: `.scratch/<effort>/map.md`
- Child tickets: `.scratch/<effort>/issues/NN-<slug>.md`
- Blocking: record dependencies with a `Blocked by: NN, NN` line
- Frontier: use the first open, unblocked, unclaimed ticket by number
- Claim: set `Status: claimed` before work
- Resolve: append the answer, set `Status: resolved`, and update the map
