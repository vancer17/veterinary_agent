"""Contracts for the twelfth support-first graph ranking repair round."""

from __future__ import annotations

from enum import StrEnum

V12_REPORT_VERSION = "v12-experiment-report-1"
V12_GRAPH_SCHEMA_VERSION = "v12-support-graph-20260831-1"
V12_ANCHOR_ELIGIBILITY_VERSION = "v12-anchor-eligibility-20260831-1"
V12_CONFLICT_RESOLUTION_VERSION = "v12-role-conflict-20260831-1"
V12_VIEW_VERSION = "v12-support-first-role-view-20260831-1"
V12_SEED_VERSION = "v12-support-first-seed-20260831-1"
V12_MACRO_SCHEMA_VERSION = "v11-macro-seeded-1+support-first-view"
V12_MACRO_PROMPT_VERSION = "v12-macro-support-first-dev-20260831-1"


class V12ExperimentId(StrEnum):
    METRIC_ALIGN = "METRIC-ALIGN"
    GRAPH_REDUCE = "GRAPH-REDUCE"
    ANCHOR_TOPO = "ANCHOR-TOPO"
    ANCHOR_NMS = "ANCHOR-NMS"
    ROLE_LOCAL_VIEW = "ROLE-LOCAL-VIEW"
    SEED_RECOVERY = "SEED-RECOVERY"
    SEED_SHARED = "SEED-SHARED"
    SEED_ACTION = "SEED-ACTION"
    MACRO_FULL = "MACRO-FULL"
    MACRO_VIEW_PRUNE = "MACRO-VIEW-PRUNE"
    REL_FROZEN_REGRESSION = "REL-FROZEN-REGRESSION"
    EARLY_MINIMAL = "EARLY-MINIMAL"
    EARLY_VOI = "EARLY-VOI"
    EARLY_FAILURE = "EARLY-FAILURE"
    REP_V12 = "REP-V12"
    NEG_V12 = "NEG-V12"
    ASYNC_V12 = "ASYNC-V12"
    HELD_OUT_V12 = "HELD-OUT-V12"


class V12ConflictVariant(StrEnum):
    NO_PRUNING = "no-pruning"
    GLOBAL_FILTER_SPANS = "global-filter-spans-negative"
    SAME_ROLE = "same-role"
    SAME_ANCHOR_ROLE = "same-anchor-role"
    SCORE_MARGIN = "score-margin"
