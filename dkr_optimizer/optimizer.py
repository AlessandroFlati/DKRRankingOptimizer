from dkr_optimizer.models import (
    LeaderboardEntry,
    Opportunity,
    OpportunityTier,
    PlayerTrackTime,
    format_time,
)

# Number of positions above the player to analyze for each tier
TIER_TARGETS = [1, 3, 5, 10, 15, 20, 25]


def compute_opportunities(
    player_times: list[PlayerTrackTime],
    leaderboards: dict[str, list[LeaderboardEntry]],
    total_tracks: int,
    player_username: str,
) -> list[Opportunity]:
    """Compute ranked optimization opportunities for the player.

    Args:
        player_times: All track times for the player (from player page).
        leaderboards: Dict mapping leaderboard key -> entries.
            Key format: "{track_slug}/{vehicle}/{category}/{laps}"
        total_tracks: Total number of track variants in the combined ranking scope.
        player_username: The player's username for finding them in leaderboards.

    Returns:
        List of Opportunity objects, sorted by best efficiency (descending).
    """
    opportunities = []

    for pt in player_times:
        lb_key = f"{pt.track_slug}/{pt.vehicle}/{pt.category}/{pt.laps}"
        entries = leaderboards.get(lb_key, [])

        if not entries:
            continue

        # Filter out default-time entries for analysis
        real_entries = [e for e in entries if not e.is_default]

        if pt.is_na:
            opp = _compute_na_opportunity(pt, real_entries, total_tracks)
        else:
            opp = _compute_existing_time_opportunity(
                pt, real_entries, total_tracks, player_username
            )

        if opp and opp.tiers:
            opportunities.append(opp)

    # Sort by best efficiency descending
    opportunities.sort(key=lambda o: o.best_efficiency, reverse=True)
    return opportunities


def _compute_na_opportunity(
    pt: PlayerTrackTime,
    real_entries: list[LeaderboardEntry],
    total_tracks: int,
) -> Opportunity:
    """Compute opportunity for a track where the player has no time (N/A).

    Submitting any time moves from effective last place to wherever they'd land.
    We estimate landing at the bottom of real submissions.
    """
    if not real_entries:
        return Opportunity(
            track_slug=pt.track_slug,
            track_name=pt.track_name,
            vehicle=pt.vehicle,
            category=pt.category,
            laps=pt.laps,
            current_rank=0,
            current_time_cs=0,
            is_na=True,
            tiers=[],
        )

    total_players = real_entries[-1].rank + 1  # rough count including unranked
    # Effective rank when N/A = total_players (last)
    effective_last_rank = total_players

    # Estimate: if they submit a time matching the worst real entry
    worst_real = real_entries[-1]
    estimated_new_rank = worst_real.rank + 1  # just below the last real entry

    positions_gained = effective_last_rank - estimated_new_rank
    af_improvement = positions_gained / total_tracks

    # For N/A tracks, time_delta is the worst real time (they need to at least submit something)
    tier = OpportunityTier(
        target_rank=estimated_new_rank,
        opponent_time_cs=worst_real.time_cs,
        target_time_cs=worst_real.time_cs,
        positions_gained=positions_gained,
        af_improvement=af_improvement,
        time_delta_cs=worst_real.time_cs,  # they need to achieve at least this
        efficiency=float("inf"),  # N/A -> any time is infinite efficiency
    )

    return Opportunity(
        track_slug=pt.track_slug,
        track_name=pt.track_name,
        vehicle=pt.vehicle,
        category=pt.category,
        laps=pt.laps,
        current_rank=effective_last_rank,
        current_time_cs=0,
        is_na=True,
        tiers=[tier],
        best_efficiency=float("inf"),
        best_tier_idx=0,
    )


def _compute_existing_time_opportunity(
    pt: PlayerTrackTime,
    real_entries: list[LeaderboardEntry],
    total_tracks: int,
    player_username: str,
) -> Opportunity | None:
    """Compute opportunity tiers for a track where the player already has a time."""
    # Find the player's position in the leaderboard
    player_entry_idx = None
    for idx, entry in enumerate(real_entries):
        if entry.username.lower() == player_username.lower():
            player_entry_idx = idx
            break

    if player_entry_idx is None:
        # Player not found in leaderboard (shouldn't happen if they have a time)
        # Fall back to using the rank from the player page
        player_rank = pt.rank
        player_time_cs = pt.time_cs
        # Find where they'd be in the sorted list
        above_entries = [e for e in real_entries if e.time_cs < player_time_cs]
    else:
        player_rank = real_entries[player_entry_idx].rank
        player_time_cs = real_entries[player_entry_idx].time_cs
        above_entries = real_entries[:player_entry_idx]

    if not above_entries or player_rank <= 1:
        # Already first place, no improvement possible
        return Opportunity(
            track_slug=pt.track_slug,
            track_name=pt.track_name,
            vehicle=pt.vehicle,
            category=pt.category,
            laps=pt.laps,
            current_rank=player_rank,
            current_time_cs=player_time_cs,
            is_na=False,
            tiers=[],
        )

    tiers = []
    best_efficiency = 0.0
    best_tier_idx = 0

    for tier_n in TIER_TARGETS:
        if tier_n > len(above_entries):
            tier_n = len(above_entries)
        if tier_n == 0:
            break

        # Target: beat the player N positions above
        target_idx = len(above_entries) - tier_n
        target_entry = above_entries[target_idx]

        # Need to beat this time by at least 1 centisecond
        target_time_cs = target_entry.time_cs - 1
        time_delta = player_time_cs - target_time_cs

        if time_delta <= 0:
            continue  # Already faster? Data inconsistency

        positions_gained = tier_n
        af_improvement = positions_gained / total_tracks
        efficiency = af_improvement / time_delta

        tier = OpportunityTier(
            target_rank=target_entry.rank,
            opponent_time_cs=target_entry.time_cs,
            target_time_cs=target_time_cs,
            positions_gained=positions_gained,
            af_improvement=af_improvement,
            time_delta_cs=time_delta,
            efficiency=efficiency,
        )
        tiers.append(tier)

        if efficiency > best_efficiency:
            best_efficiency = efficiency
            best_tier_idx = len(tiers) - 1

    return Opportunity(
        track_slug=pt.track_slug,
        track_name=pt.track_name,
        vehicle=pt.vehicle,
        category=pt.category,
        laps=pt.laps,
        current_rank=player_rank,
        current_time_cs=player_time_cs,
        is_na=False,
        tiers=tiers,
        best_efficiency=best_efficiency,
        best_tier_idx=best_tier_idx,
    )
