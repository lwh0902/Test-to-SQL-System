from __future__ import annotations

import os

from .schema_factory import SchemaSuite, build_schema_suites


DEFAULT_SEEDS = (20260726, 731921, 904177)


def gate_seeds() -> tuple[int, ...]:
    values = list(DEFAULT_SEEDS)
    extra = os.getenv("DATAPILOT_UNSEEN_SEED", "").strip()
    if extra:
        value = int(extra)
        if value not in values:
            values.append(value)
    return tuple(values)


def all_schema_suites() -> list[SchemaSuite]:
    return [suite for seed in gate_seeds() for suite in build_schema_suites(seed)]

