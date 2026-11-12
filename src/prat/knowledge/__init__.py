"""Project knowledge: what PRAT learns about each project, and how its agents use it.

See docs/design/knowledge.md. Modules:

``entries``
    The entry format and each project's knowledge repository.
``codemap``
    Files, imports and definitions of a project, ranked by personalised PageRank.
``select``
    Which entries an agent gets for a card, within a budget.
``curator``
    Applies proposed changes: screens, deduplicates, supersedes, never rewrites wholesale.
``librarian``
    The small model session that proposes changes from a finished card.
``maintain``
    The Remember phase: ingest, feedback, verify, lint, consolidate.
"""
