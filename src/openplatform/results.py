from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Metric(BaseModel):
    name: str
    value: float
    unit: str
    direction: Literal["higher", "lower", "none"]
    scope: str


class EvalError(BaseModel):
    kind: str
    message: str


class EvalResult(BaseModel):
    eval_id: str
    status: Literal["succeeded", "failed", "canceled", "interrupted", "skipped"]
    metrics: list[Metric] = Field(default_factory=list)
    error: EvalError | None = None


class ExperimentResult(BaseModel):
    run_dir: Path
    evals: list[EvalResult] = Field(default_factory=list)
    status: (
        Literal["succeeded", "failed", "partially_succeeded", "canceled", "interrupted"] | None
    ) = None
