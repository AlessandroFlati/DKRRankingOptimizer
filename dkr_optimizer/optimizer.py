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


def compute_overtake_plan(
    opportunities: list[Opportunity],
    current_af: float,
    target_af: float,
    total_tracks: int,
    target_username: str,
) -> OvertakePlan:
    """Find the minimum-cost set of improvements to overtake the target player.

    Uses multi-choice knapsack DP: for each track pick at most one tier,
    minimize total time_delta_cs while gaining enough positions to close
    the AF gap.  N/A tracks are included unconditionally (cost = 0) since
    submitting any time is qualitatively different from shaving centiseconds.
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

    # Strict inequality: need sum(positions) > gap * total_tracks
    positions_needed = math.ceil(af_gap * total_tracks + 1e-9)

    # -- Phase 1: always include all N/A tracks (free positions) --
    na_items = []
    na_positions = 0
    for opp in opportunities:
        if opp.is_na and opp.tiers:
            tier = opp.tiers[0]
            na_positions += tier.positions_gained
            na_items.append(OvertakePlanItem(
                track_slug=opp.track_slug,
                track_name=opp.track_name,
                vehicle=opp.vehicle,
                category=opp.category,
                laps=opp.laps,
                is_na=True,
                current_rank=opp.current_rank,
                current_time_cs=0,
                new_rank=tier.target_rank,
                target_time_cs=tier.target_time_cs,
                opponent_time_cs=tier.opponent_time_cs,
                positions_gained=tier.positions_gained,
                af_improvement=tier.af_improvement,
                time_delta_cs=0,
                efficiency=float("inf"),
            ))

    remaining = positions_needed - na_positions
    if remaining <= 0:
        total_gained = na_positions
        new_af = current_af - total_gained / total_tracks
        return OvertakePlan(
            target_username=target_username,
            target_af=target_af,
            current_af=current_af,
            af_gap=af_gap,
            total_positions_needed=positions_needed,
            total_positions_gained=total_gained,
            total_time_investment_cs=0,
            new_af=new_af,
            items=na_items,
            feasible=True,
        )

    # -- Phase 2: multi-choice knapsack on ranked tracks --
    # Build groups: one per track with tiers, each option = (pos, cost, opp, tier_idx)
    groups = []
    for opp in opportunities:
        if opp.is_na or not opp.tiers:
            continue
        options = []
        for tier_idx, tier in enumerate(opp.tiers):
            options.append((tier.positions_gained, tier.time_delta_cs, opp, tier_idx))
        groups.append(options)

    max_positions = sum(max(pos for pos, _, _, _ in g) for g in groups) if groups else 0
    if max_positions < remaining:
        # Not enough improvement available across all tracks
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

    cap = max_positions + 1
    INF = float("inf")

    # dp[p] = minimum total time cost to gain exactly p positions
    dp = [INF] * cap
    dp[0] = 0

    # Backtracking: prev[g][p] = (option_idx, previous_p) or (-1, p) for skip
    prev = [None] * len(groups)

    for g_idx, group in enumerate(groups):
        new_dp = [INF] * cap
        new_prev = {}

        # Option: skip this group
        for p in range(cap):
            if dp[p] < new_dp[p]:
                new_dp[p] = dp[p]
                new_prev[p] = (-1, p)

        # Option: pick one tier from this group
        for opt_idx, (pos, cost, _opp, _tidx) in enumerate(group):
            for p in range(pos, cap):
                val = dp[p - pos] + cost
                if val < new_dp[p]:
                    new_dp[p] = val
                    new_prev[p] = (opt_idx, p - pos)

        dp = new_dp
        prev[g_idx] = new_prev

    # Find the cheapest solution with positions >= remaining
    best_p = None
    best_cost = INF
    for p in range(remaining, cap):
        if dp[p] < best_cost:
            best_cost = dp[p]
            best_p = p

    if best_p is None or best_cost == INF:
        raise RuntimeError("DP found no solution despite feasibility check passing")

    # Backtrack to recover selected items
    ranked_items = []
    current_p = best_p
    for g_idx in range(len(groups) - 1, -1, -1):
        opt_idx, prev_p = prev[g_idx][current_p]
        if opt_idx >= 0:
            _pos, _cost, opp, tier_idx = groups[g_idx][opt_idx]
            tier = opp.tiers[tier_idx]
            ranked_items.append(OvertakePlanItem(
                track_slug=opp.track_slug,
                track_name=opp.track_name,
                vehicle=opp.vehicle,
                category=opp.category,
                laps=opp.laps,
                is_na=False,
                current_rank=opp.current_rank,
                current_time_cs=opp.current_time_cs,
                new_rank=tier.target_rank,
                target_time_cs=tier.target_time_cs,
                opponent_time_cs=tier.opponent_time_cs,
                positions_gained=tier.positions_gained,
                af_improvement=tier.af_improvement,
                time_delta_cs=tier.time_delta_cs,
                efficiency=tier.efficiency,
            ))
        current_p = prev_p

    # Sort all items by AF gain desc
    ranked_items.sort(key=lambda x: x.af_improvement, reverse=True)

    all_items = na_items + ranked_items
    all_items.sort(key=lambda x: x.af_improvement, reverse=True)
    total_gained = na_positions + sum(it.positions_gained for it in ranked_items)
    total_time = sum(it.time_delta_cs for it in ranked_items)
    new_af = current_af - total_gained / total_tracks

    return OvertakePlan(
        target_username=target_username,
        target_af=target_af,
        current_af=current_af,
        af_gap=af_gap,
        total_positions_needed=positions_needed,
        total_positions_gained=total_gained,
        total_time_investment_cs=total_time,
        new_af=new_af,
        items=all_items,
        feasible=True,
    )


def compute_overtake_plan_min_tracks(
    opportunities: list[Opportunity],
    current_af: float,
    target_af: float,
    total_tracks: int,
    target_username: str,
) -> OvertakePlan:
    """Find the fewest tracks to improve to overtake the target player.

    For each track, picks the tier with maximum positions gained.
    Greedy: sort by positions descending, take until gap is closed.
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

    # For each opportunity with tiers, pick the tier with max positions
    candidates = []
    for opp in opportunities:
        if not opp.tiers:
            continue
        best_tier_idx = max(
            range(len(opp.tiers)), key=lambda i: opp.tiers[i].positions_gained
        )
        best_tier = opp.tiers[best_tier_idx]
        candidates.append((opp, best_tier))

    # Sort by positions_gained descending (= max AF gain per track)
    candidates.sort(key=lambda x: x[1].positions_gained, reverse=True)

    items = []
    total_positions = 0
    total_time = 0

    for opp, tier in candidates:
        if total_positions >= positions_needed:
            break
        items.append(OvertakePlanItem(
            track_slug=opp.track_slug,
            track_name=opp.track_name,
            vehicle=opp.vehicle,
            category=opp.category,
            laps=opp.laps,
            is_na=opp.is_na,
            current_rank=opp.current_rank,
            current_time_cs=opp.current_time_cs if not opp.is_na else 0,
            new_rank=tier.target_rank,
            target_time_cs=tier.target_time_cs,
            opponent_time_cs=tier.opponent_time_cs,
            positions_gained=tier.positions_gained,
            af_improvement=tier.af_improvement,
            time_delta_cs=tier.time_delta_cs if not opp.is_na else 0,
            efficiency=tier.efficiency,
        ))
        total_positions += tier.positions_gained
        if not opp.is_na:
            total_time += tier.time_delta_cs

    feasible = total_positions >= positions_needed
    new_af = current_af - total_positions / total_tracks

    # Sort items by AF gain desc
    items.sort(key=lambda x: x.af_improvement, reverse=True)

    return OvertakePlan(
        target_username=target_username,
        target_af=target_af,
        current_af=current_af,
        af_gap=af_gap,
        total_positions_needed=positions_needed,
        total_positions_gained=total_positions,
        total_time_investment_cs=total_time,
        new_af=new_af,
        items=items,
        feasible=feasible,
    )
