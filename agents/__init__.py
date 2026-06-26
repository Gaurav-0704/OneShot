"""
agents/ - the agent framework.

  ProfileAgent      -> owns user identity, syncs from GitHub
  DiscoveryAgent    -> scrapes & ranks jobs across all platforms
  TailorAgent       -> enriches each job (research) then writes tailored resume + cover
  PackagerAgent     -> assembles ready-to-apply packets, writes to pending_review.csv
  LearnerAgent      -> post-run analysis: ATS gaps, Q&A memory, insights
  InterviewPrepAgent -> on-demand interview prep (optional, not in main loop)

All agents inherit from agents.base.Agent.
"""
from agents.profile import ProfileAgent
from agents.discovery import DiscoveryAgent
from agents.tailor import TailorAgent
from agents.packager import PackagerAgent
from agents.learner import LearnerAgent

__all__ = [
    "ProfileAgent",
    "DiscoveryAgent",
    "TailorAgent",
    "PackagerAgent",
    "LearnerAgent",
]
