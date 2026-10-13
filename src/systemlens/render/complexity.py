"""Deterministic complexity tiers used by graph node projections."""

from typing import Literal, TypedDict


class ComplexityRanking(TypedDict):
    level: Literal["low", "medium", "high"]
    rank: int
    population: int
    tier_start: int
    tier_end: int


def _complexity_ranking(relation_counts: dict[str, int]) -> dict[str, ComplexityRanking]:
    """Return the soft tercile, rank, and inclusive bounds for each resource."""
    ranked_nodes = sorted(relation_counts, key=lambda node_id: (relation_counts[node_id], node_id))
    size, remainder = divmod(len(ranked_nodes), 3)
    group_sizes = [size, size, size]
    for index in range(remainder):
        group_sizes[index] += 1

    rankings: dict[str, ComplexityRanking] = {}
    offset = 0
    levels: tuple[Literal["low", "medium", "high"], ...] = ("low", "medium", "high")
    for level, group_size in zip(levels, group_sizes):
        for rank, node_id in enumerate(ranked_nodes[offset:offset + group_size], start=offset + 1):
            rankings[node_id] = {
                "level": level,
                "rank": rank,
                "population": len(ranked_nodes),
                "tier_start": offset + 1,
                "tier_end": offset + group_size,
            }
        offset += group_size
    return rankings
