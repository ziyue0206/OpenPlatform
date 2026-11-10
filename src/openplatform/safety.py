"""Bounded physical-step JSONL recording and offline per-episode aggregation."""

import json
from dataclasses import dataclass, field
from math import dist
from pathlib import Path

EVENTS = "safety-events.jsonl"
METRICS = "safety-metrics.jsonl"
VERSION = "1"


class SafetyEvents:
    def __init__(self, directory: Path):
        self.path = directory / EVENTS
        self.path.touch(exist_ok=True)
        self.pending: list[str] = []

    def write(self, kind, **fields):
        self.pending.append(
            json.dumps({"schema_version": VERSION, "kind": kind, **fields}, allow_nan=False) + "\n"
        )
        if kind != "physics_step" or len(self.pending) >= 32:
            self.flush()

    def flush(self):
        if self.pending:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.writelines(self.pending)
            self.pending.clear()


@dataclass
class Peak:
    count: int = 0
    value: float | bool | None = None
    evidence: dict | None = None

    def observe(self, value, evidence, *, valid=True):
        self.count += int(valid)
        if self.value is None or value > self.value:
            self.value = value
            self.evidence = evidence


@dataclass
class Episode:
    episode_id: str
    env_id: int
    first_step: int | None = None
    last_step: int | None = None
    end_step: int | None = None
    complete: bool = False
    recorded: int = 0
    valid: int = 0
    baseline: dict = field(default_factory=dict)
    objects: dict = field(default_factory=dict)
    global_force: Peak = field(default_factory=Peak)
    robot_scene: Peak = field(default_factory=Peak)
    self_collision: Peak = field(default_factory=Peak)
    displacement: dict = field(default_factory=dict)

    def add(self, row, line):
        step = row["physics_step"]
        if type(step) is int:
            self.last_step = step
        self.recorded += 1
        evidence = {"file": EVENTS, "line": line, "physics_step": step}
        contacts = row.get("contacts")
        if contacts is not None:
            for peak, pairs in (
                (self.global_force, contacts),
                (self.robot_scene, [pair for pair in contacts if pair["robot_scene"]]),
            ):
                pair = max(pairs, key=lambda pair: pair["normal_force_n"], default=None)
                peak.observe(
                    pair["normal_force_n"] if pair else 0.0,
                    {**evidence, "pair": pair["bodies"] if pair else None},
                    valid=row.get("contact_status", "ok") == "ok",
                )
        collision = row.get("self_collision")
        if collision is not None:
            self.self_collision.observe(
                bool(collision),
                {**evidence, "pair": collision[0]["bodies"] if collision else None},
                valid=row.get("self_collision_status", "ok") == "ok",
            )
        poses = row.get("object_poses") or {}
        for name, baseline in self.baseline.items():
            pose = poses.get(name)
            if baseline is not None and pose is not None:
                self.displacement.setdefault(name, Peak()).observe(
                    dist(baseline["position_m"], pose["position_m"]),
                    {**evidence, "object_id": name, "baseline": baseline},
                )
        if (
            contacts is not None
            and collision is not None
            and row.get("dt_s") is not None
            and self.objects
            and all(poses.get(name) is not None for name in self.objects)
            and all(self.baseline.get(name) is not None for name in self.objects)
        ):
            self.valid += 1

    def metrics(self):
        last = self.end_step if type(self.end_step) is int else self.last_step
        expected = (
            max(0, last - self.first_step)
            if type(last) is int and type(self.first_step) is int
            else 0
        )
        expected = max(expected, self.recorded)
        exact = self.complete and type(self.first_step) is int and type(self.end_step) is int
        common = {
            "schema_version": VERSION,
            "episode_id": self.episode_id,
            "env_id": self.env_id,
            "expected_steps": expected,
            "expected_steps_status": "exact" if exact else "lower_bound",
            "recorded_steps": self.recorded,
            "episode_status": "complete" if self.complete else "partial",
        }

        def metric(name, unit, peak, *, applicable=True):
            full = exact and expected > 0 and peak.count == expected
            if not applicable:
                status, value = "not_applicable", None
            elif full:
                status, value = "ok", peak.value
            elif peak.value:
                status, value = "partial", peak.value
            else:
                status, value = "unavailable", None
            return {
                **common,
                "metric_id": name,
                "unit": unit,
                "value": value,
                "status": status,
                "valid_steps": peak.count,
                "evidence": peak.evidence if value is not None else {"file": EVENTS},
            }

        yield metric("peak_normal_contact_force", "N", self.global_force)
        yield metric("peak_robot_scene_normal_contact_force", "N", self.robot_scene)
        yield metric("self_collision_occurred", "boolean", self.self_collision)
        for name in self.objects:
            yield metric(
                f"max_baseline_displacement:{name}", "m", self.displacement.get(name, Peak())
            )
        if not any(role == "bystander" for role in self.objects.values()):
            yield metric(
                "max_bystander_baseline_displacement",
                "m",
                Peak(),
                applicable=self.first_step is None,
            )
        yield {
            **common,
            "metric_id": "collection_coverage",
            "unit": "ratio",
            "value": self.valid / expected if exact and expected else None,
            "status": (
                ("ok" if self.valid == expected else "partial")
                if exact and expected
                else ("partial" if expected else "unavailable")
            ),
            "valid_steps": self.valid,
            "evidence": {"file": EVENTS},
        }


def summarize_safety(directory: Path):
    """Aggregate the complete prefix, including a process interrupted mid-episode."""
    path = directory / EVENTS
    path.touch(exist_ok=True)
    episodes: dict[tuple, Episode] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line, text in enumerate(stream, 1):
            if not text.endswith("\n"):
                break
            row = json.loads(text)
            key = row["episode_id"], row["env_id"]
            episode = episodes.setdefault(key, Episode(*key))
            match row["kind"]:
                case "episode_open":
                    episode.objects = row["objects"]
                case "baseline":
                    episode.first_step = row["physics_step"]
                    episode.baseline = row["object_poses"]
                    episode.objects = row["objects"]
                case "physics_step":
                    episode.add(row, line)
                case "episode_end":
                    episode.end_step = row["physics_step"]
                    episode.complete = row["status"] == "complete"
    output = directory / METRICS
    pending = output.with_suffix(".tmp")
    with pending.open("w", encoding="utf-8") as stream:
        for episode in episodes.values():
            for metric in episode.metrics():
                stream.write(json.dumps(metric, allow_nan=False) + "\n")
    pending.replace(output)
