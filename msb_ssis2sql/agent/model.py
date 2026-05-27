"""Dataclasses matching the Appendix A YAML schema."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentSchedule:
    name: str
    enabled: bool
    freq_type: int
    freq_interval: int
    freq_subday_type: int
    freq_subday_interval: int
    freq_recurrence_factor: int
    active_start_date: int
    active_end_date: int
    active_start_time: int
    active_end_time: int


@dataclass
class AgentStep:
    step_id: int
    step_name: str
    subsystem: str
    command: str
    database_name: str | None
    on_success_action: int
    on_success_step_id: int
    on_fail_action: int
    on_fail_step_id: int
    retry_attempts: int
    retry_interval: int


@dataclass
class AgentJob:
    job_name: str
    enabled: bool
    description: str
    owner_login_name: str
    notify_level_email: int
    notify_email_operator: str | None
    schedules: list[AgentSchedule] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
