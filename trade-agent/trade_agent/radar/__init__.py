"""Opportunity Radar — сопоставление событий со всеми профилями компаний."""

from .matcher import MatchDetail, OpportunityRadar, match_signal, recommended_action

__all__ = ["MatchDetail", "OpportunityRadar", "match_signal", "recommended_action"]
