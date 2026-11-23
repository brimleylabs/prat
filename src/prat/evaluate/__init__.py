"""Evaluate, report, remediate: audits of a project that are no part of building a card.

A builder builds its card. Whether a codebase is secure, accessible, fast or findable is asked
separately, when a person wants to know: on a client's system they have never seen, before a
release, after a piece of work.

``library``
    The evaluations there are (security, accessibility, performance, design, search, AI
    search) and running them.
``tools``
    The scanners a security evaluation runs over the code, and how each one's report is read.
``pages``
    The evaluations of a running page: axe-core, Lighthouse, PRAT's own page checks, and what
    a server's answers say about its security.
``model``
    Findings and reports, kept privately per project and compared with the report before.
``remediate``
    From a report to cards in the backlog, and the window in which they may be worked through
    without asking again.
"""
