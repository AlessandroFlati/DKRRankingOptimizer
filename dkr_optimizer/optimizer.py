import math

from dkr_optimizer.models import (
    LeaderboardEntry,
    Opportunity,
    OpportunityTier,
    OvertakePlan,
    OvertakePlanItem,
    PlayerTrackTime,
    format_time,
)

# Number of positions above the player to analyze for each tier
TIER_TARGETS = [1, 3, 5, 10, 15, 20, 25]

# Exponential difficulty factor for overtake plans.
# weighted_cost = time_delta * exp(DIFFICULTY_K * (1 - target_rank / current_rank))
# k=5 means reaching rank 1 costs ~148x the raw cs.
DIFFICULTY_K = 5.0


def _difficulty_weight(current_rank: int, target_rank: int) -> float:
    """Exponential weight penalizing large leaderboard climbs."""
    climb = 1.0 - target_rank / current_rank
    return math.exp(DIFFICULTY_K * climb)


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


def _find_player_position(
    pt: PlayerTrackTime,
    real_entries: list[LeaderboardEntry],
    player_username: str,
) -> tuple[int, int, list[LeaderboardEntry]]:
    """Find a player's rank, time, and entries above them in a leaderboard.

    Returns (player_rank, player_time_cs, above_entries).
    """
    player_entry_idx = None
    for idx, entry in enumerate(real_entries):
        if entry.username.lower() == player_username.lower():
            player_entry_idx = idx
            break

    if player_entry_idx is None:
        player_rank = pt.rank
        player_time_cs = pt.time_cs
        above_entries = [e for e in real_entries if e.time_cs < player_time_cs]
    else:
        player_rank = real_entries[player_entry_idx].rank
        player_time_cs = real_entries[player_entry_idx].time_cs
        above_entries = real_entries[:player_entry_idx]

    return player_rank, player_time_cs, above_entries


def _compute_tiers(
    above_entries: list[LeaderboardEntry],
    player_time_cs: int,
    total_tracks: int,
    tier_targets: list[int],
) -> list[OpportunityTier]:
    """Compute tiers for the given position targets."""
    tiers = []
    seen_targets = set()

    for tier_n in tier_targets:
        if tier_n > len(above_entries):
            tier_n = len(above_entries)
        if tier_n == 0:
            break
        if tier_n in seen_targets:
            continue
        seen_targets.add(tier_n)

        target_idx = len(above_entries) - tier_n
        target_entry = above_entries[target_idx]

        target_time_cs = target_entry.time_cs - 1
        time_delta = player_time_cs - target_time_cs

        if time_delta <= 0:
            continue

        positions_gained = tier_n
        af_improvement = positions_gained / total_tracks
        efficiency = af_improvement / time_delta

        tiers.append(OpportunityTier(
            target_rank=target_entry.rank,
            opponent_time_cs=target_entry.time_cs,
            target_time_cs=target_time_cs,
            positions_gained=positions_gained,
            af_improvement=af_improvement,
            time_delta_cs=time_delta,
            efficiency=efficiency,
        ))

    return tiers


def _compute_existing_time_opportunity(
    pt: PlayerTrackTime,
    real_entries: list[LeaderboardEntry],
    total_tracks: int,
    player_username: str,
) -> Opportunity | None:
    """Compute opportunity tiers for a track where the player already has a time."""
    player_rank, player_time_cs, above_entries = _find_player_position(
        pt, real_entries, player_username
    )

    if not above_entries or player_rank <= 1:
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

    tiers = _compute_tiers(above_entries, player_time_cs, total_tracks, TIER_TARGETS)

    best_efficiency = 0.0
    best_tier_idx = 0
    for i, t in enumerate(tiers):
        if t.efficiency > best_efficiency:
            best_efficiency = t.efficiency
            best_tier_idx = i

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


def _build_overtake_groups(
    player_times: list[PlayerTrackTime],
    leaderboards: dict[str, list[LeaderboardEntry]],
    total_tracks: int,
    player_username: str,
) -> tuple[list[OvertakePlanItem], list[list[tuple]]]:
    """Build N/A items and ranked groups with ALL possible tiers for overtake plans.

    Returns (na_items, groups) where each group is a list of
    (positions_gained, time_delta_cs, plan_item) tuples.
    """
    na_items = []
    groups = []

    for pt in player_times:
        lb_key = f"{pt.track_slug}/{pt.vehicle}/{pt.category}/{pt.laps}"
        entries = leaderboards.get(lb_key, [])
        if not entries:
            continue

        real_entries = [e for e in entries if not e.is_default]

        if pt.is_na:
            if not real_entries:
                continue
            total_players = real_entries[-1].rank + 1
            effective_last_rank = total_players
            worst_real = real_entries[-1]
            estimated_new_rank = worst_real.rank + 1
            positions_gained = effective_last_rank - estimated_new_rank
            if positions_gained <= 0:
                continue
            na_items.append(OvertakePlanItem(
                track_slug=pt.track_slug,
                track_name=pt.track_name,
                vehicle=pt.vehicle,
                category=pt.category,
                laps=pt.laps,
                is_na=True,
                current_rank=effective_last_rank,
                current_time_cs=0,
                new_rank=estimated_new_rank,
                target_time_cs=worst_real.time_cs,
                opponent_time_cs=worst_real.time_cs,
                positions_gained=positions_gained,
                af_improvement=positions_gained / total_tracks,
                time_delta_cs=0,
                efficiency=float("inf"),
            ))
            continue

        player_rank, player_time_cs, above_entries = _find_player_position(
            pt, real_entries, player_username
        )
        if not above_entries or player_rank <= 1:
            continue

        # Compute ALL possible tiers: beat +1, +2, ..., +N
        all_targets = list(range(1, len(above_entries) + 1))
        tiers = _compute_tiers(above_entries, player_time_cs, total_tracks, all_targets)
        if not tiers:
            continue

        options = []
        for tier in tiers:
            item = OvertakePlanItem(
                track_slug=pt.track_slug,
                track_name=pt.track_name,
                vehicle=pt.vehicle,
                category=pt.category,
                laps=pt.laps,
                is_na=False,
                current_rank=player_rank,
                current_time_cs=player_time_cs,
                new_rank=tier.target_rank,
                target_time_cs=tier.target_time_cs,
                opponent_time_cs=tier.opponent_time_cs,
                positions_gained=tier.positions_gained,
                af_improvement=tier.af_improvement,
                time_delta_cs=tier.time_delta_cs,
                efficiency=tier.efficiency,
            )
            options.append((tier.positions_gained, tier.time_delta_cs, item))
        groups.append(options)

    return na_items, groups


def compute_overtake_plan(
    player_times: list[PlayerTrackTime],
    leaderboards: dict[str, list[LeaderboardEntry]],
    current_af: float,
    target_af: float,
    total_tracks: int,
    player_username: str,
    target_username: str,
) -> OvertakePlan:
    """Find the minimum-cost set of improvements to overtake the target player.

    Uses multi-choice knapsack DP with ALL possible position jumps per track
    (not just the 7 predefined tiers). For each track, considers beating +1,
    +2, ..., +N players above. N/A tracks are included unconditionally
    (cost = 0) since submitting any time is qualitatively different from
    shaving centiseconds.
    """
    af_gap = current_af - target_af
    if af_gap <= 0:
        return OvertakePlan(
            target_username=target_username,
            target_af=target_af,
            current_af=current_af,
            af_gap=0.0,
            total_positions_needed=0,
            total_positions_gained=0,
            total_time_investment_cs=0,
            new_af=current_af,
            feasible=True,
        )

    positions_needed = math.ceil(af_gap * total_tracks + 1e-9)

    na_items, groups = _build_overtake_groups(
        player_times, leaderboards, total_tracks, player_username
    )
    na_positions = sum(it.positions_gained for it in na_items)

    remaining = positions_needed - na_positions
    if remaining <= 0:
        total_gained = na_positions
        na_items.sort(key=lambda x: x.af_improvement, reverse=True)
        return OvertakePlan(
            target_username=target_username,
            target_af=target_af,
            current_af=current_af,
            af_gap=af_gap,
            total_positions_needed=positions_needed,
            total_positions_gained=total_gained,
            total_time_investment_cs=0,
            new_af=current_af - total_gained / total_tracks,
            items=na_items,
            feasible=True,
        )

    max_positions = sum(max(pos for pos, _, _ in g) for g in groups) if groups else 0
    if max_positions < remaining:
        return OvertakePlan(
            target_username=target_username,
            target_af=target_af,
            current_af=current_af,
            af_gap=af_gap,
            total_positions_needed=positions_needed,
            total_positions_gained=na_positions,
            total_time_investment_cs=0,
            new_af=current_af - na_positions / total_tracks,
            items=na_items,
            feasible=False,
        )

    # Apply exponential difficulty weighting to DP costs
    weighted_groups = []
    for group in groups:
        weighted = []
        for pos, cost, item in group:
            weight = _difficulty_weight(item.current_rank, item.new_rank)
            weighted.append((pos, cost * weight, item))
        weighted_groups.append(weighted)

    cap = max_positions + 1
    INF = float("inf")

    dp = [INF] * cap
    dp[0] = 0
    prev = [None] * len(weighted_groups)

    for g_idx, group in enumerate(weighted_groups):
        new_dp = [INF] * cap
        new_prev = {}

        for p in range(cap):
            if dp[p] < new_dp[p]:
                new_dp[p] = dp[p]
                new_prev[p] = (-1, p)

        for opt_idx, (pos, cost, _item) in enumerate(group):
            for p in range(pos, cap):
                val = dp[p - pos] + cost
                if val < new_dp[p]:
                    new_dp[p] = val
                    new_prev[p] = (opt_idx, p - pos)

        dp = new_dp
        prev[g_idx] = new_prev

    best_p = None
    best_cost = INF
    for p in range(remaining, cap):
        if dp[p] < best_cost:
            best_cost = dp[p]
            best_p = p

    if best_p is None or best_cost == INF:
        raise RuntimeError("DP found no solution despite feasibility check passing")

    ranked_items = []
    current_p = best_p
    for g_idx in range(len(weighted_groups) - 1, -1, -1):
        opt_idx, prev_p = prev[g_idx][current_p]
        if opt_idx >= 0:
            _pos, _cost, item = weighted_groups[g_idx][opt_idx]
            ranked_items.append(item)
        current_p = prev_p

    all_items = na_items + ranked_items
    all_items.sort(key=lambda x: x.af_improvement, reverse=True)
    total_gained = na_positions + sum(it.positions_gained for it in ranked_items)
    total_time = sum(it.time_delta_cs for it in ranked_items)

    return OvertakePlan(
        target_username=target_username,
        target_af=target_af,
        current_af=current_af,
        af_gap=af_gap,
        total_positions_needed=positions_needed,
        total_positions_gained=total_gained,
        total_time_investment_cs=total_time,
        new_af=current_af - total_gained / total_tracks,
        items=all_items,
        feasible=True,
    )


def compute_overtake_plan_min_tracks(
    player_times: list[PlayerTrackTime],
    leaderboards: dict[str, list[LeaderboardEntry]],
    current_af: float,
    target_af: float,
    total_tracks: int,
    player_username: str,
    target_username: str,
) -> OvertakePlan:
    """Find the fewest tracks to improve to overtake the target player.

    For each track, picks the tier with the best difficulty-adjusted value
    (positions / exponential_weight) rather than raw max positions.  This
    avoids selecting unrealistically large leaderboard climbs.
    Greedy: sort by selected positions descending, take until gap is closed.
    """
    af_gap = current_af - target_af
    if af_gap <= 0:
        return OvertakePlan(
            target_username=target_username,
            target_af=target_af,
            current_af=current_af,
            af_gap=0.0,
            total_positions_needed=0,
            total_positions_gained=0,
            total_time_investment_cs=0,
            new_af=current_af,
            feasible=True,
        )

    positions_needed = math.ceil(af_gap * total_tracks + 1e-9)

    na_items, groups = _build_overtake_groups(
        player_times, leaderboards, total_tracks, player_username
    )

    # For each ranked group, pick the tier with best positions/difficulty ratio
    candidates = []
    for group in groups:
        best = max(
            group,
            key=lambda x: x[0] / _difficulty_weight(x[2].current_rank, x[2].new_rank),
        )
        candidates.append(best[2])
    for item in na_items:
        candidates.append(item)

    # Sort by positions_gained descending
    candidates.sort(key=lambda x: x.positions_gained, reverse=True)

    items = []
    total_positions = 0
    total_time = 0

    for item in candidates:
        if total_positions >= positions_needed:
            break
        items.append(item)
        total_positions += item.positions_gained
        total_time += item.time_delta_cs

    feasible = total_positions >= positions_needed

    items.sort(key=lambda x: x.af_improvement, reverse=True)

    return OvertakePlan(
        target_username=target_username,
        target_af=target_af,
        current_af=current_af,
        af_gap=af_gap,
        total_positions_needed=positions_needed,
        total_positions_gained=total_positions,
        total_time_investment_cs=total_time,
        new_af=current_af - total_positions / total_tracks,
        items=items,
        feasible=feasible,
    )
