<!--
## Sync Impact Report
- Version change: n/a (initial ratification) → 1.0.0
- Modified principles: none (initial ratification)
- Added sections: Core Principles (5), Engineering Standards, Git Workflow, Governance
- Removed sections: none
- Follow-up TODOs: none
-->

# New Agent Constitution

## Core Principles

### I. Plugin-First Architecture

Every capability is delivered as a dynamically loaded plugin/toolset. Plugins
define their own available tools and parameters and are loaded via configuration.
The core engine MUST stay agnostic of specific tool details and communicate only
through the plugin interfaces.

*Rationale*: keeps the core stable while the toolset grows without core churn.

### II. CLI Interface

Every feature MUST be exposed through the Typer-based CLI (`run` / `chat` /
`serve` / `toolset` / `version`). The CLI consumes only the `StreamMessage`
event stream and MUST NOT reach into `Tool`/`Toolset` internals.

*Rationale*: decoupling keeps the CLI thin, scriptable, and independently testable.

### III. Config Backward Compatibility

Config renames MUST be handled via Pydantic `extra="allow"` plus a
`model_validator` that maps old field names to new ones. Deprecated fields MUST
NOT remain in the schema.

*Rationale*: existing user config files keep working across upgrades without a
forced migration.

### IV. Test-First (NON-NEGOTIABLE)

New features require unit tests; new plugins require integration tests. Test
files mirror the source layout (`tests/` mirrors `src/`). HTTP mocking MUST use
the `responses` library, never `@patch("requests.get")`. LLM-dependent tests
MUST be tagged `llm` and run separately from the offline suite.

*Rationale*: keeps the offline test suite deterministic and fast while coverage
stays aligned with source structure.

### V. Generalization over Specialization

New fields and methods MUST live at the most general level of the class
hierarchy, not narrowed to a subclass referenced by a single issue. Retry logic
MUST use the `tenacity` library; hand-written retry loops are prohibited.

*Rationale*: prevents one-off fixes from fragmenting the design and duplicating
plumbing.

## Engineering Standards

- Imports go at the top of the file; no function-level imports.
- Type annotations are required (mypy enforced).
- Do NOT run pre-commit / ruff / mypy unless the user explicitly asks.
- Keep changes minimal and matched to the surrounding code style.

## Git Workflow

- Commit with `git commit -s --no-verify` (sign-off + skip local pre-commit).
- Only create new commits; never amend.
- Only merge; never rebase.
- Only push; never force push.
- Keep the full commit history intact for rollback.

## Governance

This constitution supersedes all other development practices. Amendments require
documentation of the change, approval, and a migration plan when behavior
changes. Version bumps follow semantic versioning: MAJOR for backward
incompatible principle removals or redefinitions, MINOR for new principles or
sections, PATCH for clarifications and wording. All PRs and reviews MUST verify
compliance with this constitution. Use `CLAUDE.md` as the runtime development
guidance file.

**Version**: 1.0.0 | **Ratified**: 2026-09-02 | **Last Amended**: 2026-09-02
