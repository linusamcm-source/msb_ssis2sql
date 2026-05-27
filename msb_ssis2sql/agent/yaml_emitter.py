"""Emit an AgentJob as a deterministic YAML string (Appendix A schema)."""
from __future__ import annotations

from dataclasses import asdict

import yaml

from .model import AgentJob


def emit_job_yaml(job: AgentJob) -> str:
    """Return ``yaml.safe_dump(asdict(job), sort_keys=True, default_flow_style=False)``."""
    return yaml.safe_dump(asdict(job), sort_keys=True, default_flow_style=False)
