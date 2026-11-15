"""One run's crew session: who leads it, what it carries, what it remembers.

:func:`prepare` is called by the engine before the lead's session starts. It picks the lead
(prat.crew), builds the skill loadout, briefs the lead and each helper from their own memory
(prat.memory) and builds PRAT's tools for the session. While the session runs,
:meth:`CrewSession.observe` encodes the lead's thoughts into its working memory and each
helper's finished work into the helper's memory; :meth:`CrewSession.finish` turns the lead's
reflection (or, without one, the run's own record) into the episode it will remember.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat import crew, identity
from prat.engine import agent_tools
from prat.memory import AgentMemory, Feeling, briefing, new_episode
from prat.projects import ProjectSpec
from prat.runtime.base import (
    CREW_TOOLS,
    HelperEvent,
    HelperSpec,
    PratTool,
    RuntimeEvent,
    TasksEvent,
    ThoughtEvent,
)

log = logging.getLogger(__name__)
HELPER_BRIEF_CHARS = 1200


@dataclass
class CrewSession:
    member: crew.Member
    skills: list[str]
    helpers: dict[str, HelperSpec]
    skill_dirs: tuple[Path, ...]
    prat_tools: tuple[PratTool, ...]
    prompt: str
    tool_ctx: agent_tools.ToolContext
    thinking: bool
    title: str
    role: str
    thoughts: list[str] = field(default_factory=list)
    tasks: list[tuple[str, str]] = field(default_factory=list)

    @property
    def agent(self) -> str:
        """Where the lead's memory lives: its stack, whatever name it chose."""
        return self.member.key

    @property
    def tools(self) -> frozenset[str]:
        return CREW_TOOLS

    def observe(self, event: RuntimeEvent) -> None:
        """Encode what happens into memory. Never raises."""
        try:
            if isinstance(event, ThoughtEvent):
                self.thoughts.append(event.text)
                AgentMemory(self.tool_ctx.home, self.agent).note(
                    self.tool_ctx.project, self.tool_ctx.card_id, "thought", event.text
                )
            elif isinstance(event, TasksEvent):
                self.tasks = [(t.subject, t.status) for t in event.tasks]
            elif isinstance(event, HelperEvent) and event.phase == "finished":
                helper = AgentMemory(self.tool_ctx.home, event.name)
                helper.save(
                    new_episode(
                        event.name,
                        project=self.tool_ctx.project,
                        card_id=self.tool_ctx.card_id,
                        run_id=self.tool_ctx.run_id,
                        role="helper",
                        title=f"{self.title}: asked by {self.member.name}",
                        what=f"Asked: {event.prompt}. Answered: {event.summary}"[:1500],
                        feeling=Feeling("helpful", 0.3, 0.3)
                        if event.ok
                        else Feeling("stuck", -0.4, 0.4),
                        importance=0.3,
                    )
                )
        except Exception:
            log.exception("could not record a memory for %s", self.agent)

    def finish(self, outcome: str, message: str, files_changed: int | None = None) -> None:
        """The lead's episode of this session: its reflection, or the run's own record."""
        try:
            reflection = self.tool_ctx.reflection
            done = [subject for subject, status in self.tasks if status == "completed"]
            what = reflection.what or " ".join(
                part
                for part in (
                    f"Tasks done: {', '.join(done)}." if done else "",
                    f"Changed {files_changed} files." if files_changed else "",
                    message[:600],
                )
                if part
            )
            reasoning = reflection.reasoning or " / ".join(t[:200] for t in self.thoughts[-3:])
            state = {"completed": "pending", "blocked": "blocked", "cancelled": "cancelled"}.get(
                outcome, "failed"
            )
            importance = 0.5 + (0.2 if self.tool_ctx.risks else 0.0)
            if reflection.confidence is not None and reflection.confidence < 0.5:
                importance += 0.1
            AgentMemory(self.tool_ctx.home, self.agent).save(
                new_episode(
                    self.agent,
                    project=self.tool_ctx.project,
                    card_id=self.tool_ctx.card_id,
                    run_id=self.tool_ctx.run_id,
                    role=self.role,
                    title=self.title,
                    what=what,
                    reasoning=reasoning,
                    lesson=reflection.lesson,
                    intention=reflection.intention,
                    feeling=reflection.feeling,
                    outcome=state,
                    importance=min(1.0, importance),
                    tags=[s for s in self.skills if s not in crew.CORE_SKILLS],
                )
            )
        except Exception:
            log.exception("could not record the episode of %s", self.agent)


def prepare(
    *,
    home: Path,
    project: str,
    spec: ProjectSpec,
    worktree: Path,
    card_id: int,
    run_id: int,
    title: str,
    card_text: str,
    role: str,
    record: Callable[[str, dict[str, Any]], Any],
    live: Callable[[str, Any], None],
    databases: Mapping[str, str],
    start_job: Callable[[str], Awaitable[str]] | None = None,
    job_status: Callable[[int | None], Awaitable[str]] | None = None,
) -> CrewSession:
    crew_spec = spec.crew.model_dump()
    member = crew.lead_for(crew_spec, worktree)
    skills = crew.loadout(member, crew_spec, home, project)
    loadout_dir = crew.build_loadout(home, project, skills) if skills else None
    query = f"{title}\n{card_text}"
    who = identity.of(home, member.key)
    helper_briefs = {}
    for name in spec.crew.helpers or crew.HELPER_PROMPTS:
        helper = identity.of(home, name)
        remembered = briefing(
            AgentMemory(home, name),
            project=project,
            card_id=None,
            query=query,
            budget=HELPER_BRIEF_CHARS,
            episodes=2,
            name=helper.name,
        )
        helper_briefs[name] = "\n\n".join(t for t in (helper.persona(), remembered) if t)
    helpers = crew.helpers(spec.crew.helpers, helper_briefs)
    memory = briefing(
        AgentMemory(home, member.key), project=project, card_id=card_id, query=query, name=who.name
    )
    tool_ctx = agent_tools.ToolContext(
        home=home,
        project=project,
        card_id=card_id,
        run_id=run_id,
        agent=member.key,
        record=record,
        live=live,
        databases=dict(databases),
        start_job=start_job,
        job_status=job_status,
    )
    phases = crew.skills_by_phase(home, project, skills)
    names = {name: identity.of(home, name).name for name in helpers}
    prompt = (
        who.persona()
        + "\n\n"
        + crew.crew_prompt(member, helpers, phases, helper_names=names)
        + (f"\n\n{memory}" if memory else "")
    )
    live(
        "lead",
        {"name": who.name, "key": member.key, "stack": member.stack, "color": member.color},
    )
    live("helper_names", names)
    live("skills_carried", skills)
    return CrewSession(
        member=member,
        skills=skills,
        helpers=helpers,
        skill_dirs=(loadout_dir,) if loadout_dir else (),
        prat_tools=agent_tools.build(tool_ctx),
        prompt=prompt,
        tool_ctx=tool_ctx,
        thinking=spec.crew.thinking,
        title=title,
        role=role,
    )
