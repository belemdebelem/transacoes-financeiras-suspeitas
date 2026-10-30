"""Configurable thresholds and scores that drive the risk rules.

Every parameter that influences a classification lives here, so the rules can
be tuned without touching the rule logic itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleConfig:
    """Thresholds, scores and classification bands used by the risk rules."""

    high_amount_threshold: float = 10_000.0
    high_amount_score: int = 50

    atm_amount_threshold: float = 5_000.0
    atm_score: int = 20

    burst_window_seconds: int = 600
    burst_min_transactions: int = 3
    burst_amount_threshold: float = 8_000.0
    burst_score: int = 50

    unusual_amount_threshold: float = 5_000.0
    unusual_channel_share: float = 0.30
    unusual_location_share: float = 0.30
    unusual_channel_score: int = 20
    unusual_location_score: int = 20

    min_baseline_samples: int = 6
    dominance_threshold: float = 0.50

    combination_score: int = 15

    critical_score: int = 75
    suspect_score: int = 50
    attention_score: int = 20
    alert_score: int = 50

    max_score: int = 100

    def risk_level_for(self, score: int) -> str:
        """Map a risk score to its classification band."""
        if score >= self.critical_score:
            return "CRITICAL"
        if score >= self.suspect_score:
            return "SUSPECT"
        if score >= self.attention_score:
            return "ATTENTION"
        return "NORMAL"


DEFAULT_CONFIG = RuleConfig()
