"""
agents/base.py - abstract Agent class.

All agents share:
  - a name (for logging)
  - a role / system prompt (used when they call an LLM)
  - access to the UserProfile (read-only after construction)
  - a logger
  - optional dry_run flag

Agents don't share state directly. They communicate by passing JobApplication
objects through the orchestrator.
"""
from __future__ import annotations

import logging
from abc import ABC
from typing import Optional

from models import UserProfile


class Agent(ABC):
    """Base class. Every concrete agent overrides `name` and `role`."""

    name: str = "agent"
    role: str = "You are a helpful agent."

    def __init__(self, profile: Optional[UserProfile] = None, *, dry_run: bool = True):
        self.profile = profile
        self.dry_run = dry_run
        self.log = logging.getLogger(f"agent.{self.name}")

    def with_profile(self, profile: UserProfile) -> "Agent":
        """Return self with profile attached. Convenience for orchestrator."""
        self.profile = profile
        return self

    def info(self, msg: str) -> None:
        self.log.info(msg)

    def warn(self, msg: str) -> None:
        self.log.warning(msg)

    def debug(self, msg: str) -> None:
        self.log.debug(msg)
