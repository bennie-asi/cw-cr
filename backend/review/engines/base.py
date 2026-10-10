from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from review.models import ModelConfig, Project


@dataclass(frozen=True)
class ReviewContext:
    project: Project | None
    title: str
    branch: str
    mr_iid: int
    mr_url: str
    mode: str
    diff: str
    prompt: str
    model_config: ModelConfig | None
    timeout: int
    job_id: str
    repository: Path | None = None
    base_ref: str = 'cwcr-base'
    head_ref: str = 'cwcr-head'


class ReviewEngineAdapter:
    engine_id = ''

    def runtime_version(self) -> str:
        raise NotImplementedError

    def analyze(self, context: ReviewContext) -> dict:
        raise NotImplementedError
