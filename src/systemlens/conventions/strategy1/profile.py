"""Activation helpers for the Strategy1 convention pack."""

from typing import Literal


TopicStrategy = Literal["default", "strategy1"]


def is_enabled(strategy: TopicStrategy | str) -> bool:
    """Return whether the opt-in Strategy1 convention pack is active."""
    return strategy == "strategy1"
