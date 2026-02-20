import argparse
import os
import sys

import yaml

from dkr_optimizer.models import LeaderboardEntry, format_time, parse_time
from dkr_optimizer.optimizer import (
    compute_opportunities,
    compute_overtake_plan,
    compute_overtake_plan_min_tracks,
)
from dkr_optimizer.parser import (
    parse_combined_ranking,
    parse_leaderboard,
    parse_player_page,
)
from dkr_optimizer.report import generate_reports
from dkr_optimizer.scraper import DKRScraper


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _apply_time_overrides(player_times, leaderboards, overrides_raw, username):
    """Apply user-supplied time overrides to player_times and leaderboards.

    Returns the total AF delta (negative = improvement).
    """
    total_rank_delta = 0
    tracks_affected = 0

    for ovr in overrides_raw:
        track = ovr["track"]
        vehicle = ovr["vehicle"]
        category = ovr.get("category", "standard")
        laps = ovr["laps"]
        new_time_cs = parse_time(ovr["time"])
        lb_key = f"{track}/{vehicle}/{category}/{laps}"

        # Find matching PlayerTrackTime
        pt_match = None
        for pt in player_times:
            if (pt.track_slug == track and pt.vehicle == vehicle
                    and pt.category == category and pt.laps == laps):
                pt_match = pt
                break

        if pt_match is None:
            print(f"  WARNING: Override for {lb_key} has no matching player track")
            continue

        entries = leaderboards.get(lb_key)
        if not entries:
            print(f"  WARNING: No leaderboard for {lb_key}")
            continue

        old_time = pt_match.time_cs

        # Find or create player entry in leaderboard
        player_entry = None
        for e in entries:
            if e.username.lower() == username.lower():
                player_entry = e
                break

        old_rank = player_entry.rank if player_entry else pt_match.rank

        if player_entry:
            player_entry.time_cs = new_time_cs
        else:
            player_entry = LeaderboardEntry(
                rank=0,
                username=username,
                display_name=username,
                time_cs=new_time_cs,
                is_default=False,
            )
            entries.append(player_entry)

        # Re-sort: real entries by time, then defaults
        entries.sort(key=lambda e: (e.is_default, e.time_cs))

        # Re-assign ranks
        rank = 1
        for i, e in enumerate(entries):
            if e.is_default:
                e.rank = rank
            else:
                if i > 0 and not entries[i - 1].is_default and entries[i - 1].time_cs == e.time_cs:
                    e.rank = entries[i - 1].rank
                else:
                    e.rank = rank
                rank += 1

        new_rank = player_entry.rank
        pt_match.time_cs = new_time_cs
        pt_match.rank = new_rank
        pt_match.is_na = False

        total_rank_delta += new_rank - old_rank
        tracks_affected += 1
        print(f"  {lb_key}: {format_time(old_time)} -> {format_time(new_time_cs)}, "
              f"rank {old_rank} -> {new_rank}")

    return total_rank_delta, tracks_affected


def main():
    parser = argparse.ArgumentParser(description="DKR Ranking Optimizer")
    parser.add_argument("--user", help="Player username (overrides config.yaml)")
    parser.add_argument("--clear-cache", action="store_true", help="Clear cached data before running")
    parser.add_argument("--cache-ttl", type=float, help="Cache TTL in hours (overrides config.yaml)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    config = load_config(args.config)
    username = args.user or config["username"]
    cache_ttl = args.cache_ttl if args.cache_ttl is not None else config.get("cache_ttl_hours", 24)
    delay = config.get("request_delay_seconds", 0.5)
    output_dir = config.get("output_dir", "output")

    scraper = DKRScraper(
        cache_dir="cache",
        cache_ttl_hours=cache_ttl,
        request_delay=delay,
    )

    if args.clear_cache:
        print("Clearing cache...")
        scraper.clear_cache()

    # Step 1: Fetch and parse player page
    print(f"Fetching player page for {username}...")
    player_html = scraper.fetch(scraper.player_url(username))
    profile, player_times = parse_player_page(player_html)
    print(f"  Combined rank: #{profile.combined_rank}")
    na_count = sum(1 for t in player_times if t.is_na)
    has_count = sum(1 for t in player_times if not t.is_na)
    print(f"  Track times: {has_count} submitted, {na_count} N/A")

    # Step 2: Fetch and parse combined ranking
    print("Fetching combined ranking...")
    ranking_html = scraper.fetch(scraper.combined_ranking_url())
    ranking = parse_combined_ranking(ranking_html)
    player_ranking = next(
        (r for r in ranking if r.username.lower() == username.lower()),
        None,
    )
    if player_ranking:
        current_af = player_ranking.af
        current_rank = player_ranking.rank
        print(f"  AF: {current_af}, Rank: #{current_rank}")
    else:
        current_af = profile.current_af
        current_rank = profile.combined_rank
        print(f"  Player not found in combined ranking, using profile data: AF={current_af}")

    # Step 3: Determine which track variants to scrape
    # We need leaderboards for all track variants where the player has a time OR is N/A
    track_variants = []
    for pt in player_times:
        key = (pt.track_slug, pt.vehicle, pt.category, pt.laps)
        track_variants.append(key)

    total_tracks = len(track_variants)
    print(f"\nFetching {total_tracks} leaderboards...")

    # Step 4: Fetch all leaderboards
    leaderboards = {}
    skipped = 0
    fetched = 0
    for i, (slug, vehicle, category, laps) in enumerate(track_variants):
        lb_key = f"{slug}/{vehicle}/{category}/{laps}"
        url = scraper.leaderboard_url(slug, vehicle, category, laps)
        progress = f"[{i+1}/{total_tracks}]"

        html = scraper.fetch(url)
        if html is None:
            skipped += 1
            sys.stdout.write(f"\r  {progress} {lb_key} - n/a (no leaderboard)       ")
            sys.stdout.flush()
            continue

        entries = parse_leaderboard(html)
        leaderboards[lb_key] = entries
        fetched += 1

        real = sum(1 for e in entries if not e.is_default)
        sys.stdout.write(f"\r  {progress} {lb_key} - {real} entries       ")
        sys.stdout.flush()

    print(f"\n  Fetched: {fetched} leaderboards, skipped: {skipped} non-existent")

    # Only count tracks that actually exist as leaderboards
    valid_tracks = len(leaderboards)
    print(f"  Track variants in scope: {valid_tracks}")

    # Step 4b: Apply time overrides (new times not yet on dkr64.com)
    overrides_raw = config.get("time_overrides", [])
    if overrides_raw:
        print(f"\nApplying {len(overrides_raw)} time overrides...")
        rank_delta, n_affected = _apply_time_overrides(
            player_times, leaderboards, overrides_raw, username
        )
        if n_affected > 0:
            af_delta = rank_delta / valid_tracks
            old_af = current_af
            current_af = current_af + af_delta
            print(f"  AF adjusted: {old_af} -> {current_af:.3f} (delta {af_delta:+.4f})")

    # Step 5: Compute opportunities
    print("\nComputing optimization opportunities...")
    opportunities = compute_opportunities(
        player_times=player_times,
        leaderboards=leaderboards,
        total_tracks=valid_tracks,
        player_username=username,
    )

    # Step 6: Compute overtake plans
    exclude_raw = config.get("exclude_from_plans", [])
    exclude = [(e["track"], e["vehicle"]) for e in exclude_raw] if exclude_raw else None

    overtake_min_time = None
    overtake_min_tracks = None
    if current_rank > 1:
        target_entry = next(
            (r for r in ranking if r.rank == current_rank - 1),
            None,
        )
        if target_entry:
            print(f"\nComputing overtake plans to beat #{target_entry.rank} {target_entry.username} (AF {target_entry.af})...")
            if exclude:
                print(f"  Excluding {len(exclude)} track/vehicle combos from plans")
            overtake_min_time = compute_overtake_plan(
                player_times=player_times,
                leaderboards=leaderboards,
                current_af=current_af,
                target_af=target_entry.af,
                total_tracks=valid_tracks,
                player_username=username,
                target_username=target_entry.username,
                exclude=exclude,
            )
            overtake_min_tracks = compute_overtake_plan_min_tracks(
                player_times=player_times,
                leaderboards=leaderboards,
                current_af=current_af,
                target_af=target_entry.af,
                total_tracks=valid_tracks,
                player_username=username,
                target_username=target_entry.username,
                exclude=exclude,
            )
            if overtake_min_time.feasible:
                print(f"  Min time:   {len(overtake_min_time.items)} tracks, {format_time(overtake_min_time.total_time_investment_cs)} total improvement")
                print(f"  Min tracks: {len(overtake_min_tracks.items)} tracks, {format_time(overtake_min_tracks.total_time_investment_cs)} total improvement")
            else:
                print("  Not enough improvement available to overtake.")

    # Step 7: Generate reports
    print("\nGenerating reports...")
    html_path, json_path = generate_reports(
        profile=profile,
        current_af=current_af,
        current_rank=current_rank,
        opportunities=opportunities,
        total_tracks=valid_tracks,
        output_dir=output_dir,
        overtake_min_time=overtake_min_time,
        overtake_min_tracks=overtake_min_tracks,
    )

    # Summary
    na_opps = [o for o in opportunities if o.is_na]
    ranked_opps = [o for o in opportunities if not o.is_na and o.tiers]

    print(f"\n{'='*60}")
    print(f"  Player:         {username}")
    print(f"  Combined Rank:  #{current_rank}")
    print(f"  Average Finish: {current_af}")
    print(f"  N/A tracks:     {len(na_opps)} (submit any time for big AF boost)")
    print(f"  Improvable:     {len(ranked_opps)} tracks")
    print(f"{'='*60}")

    if na_opps:
        print(f"\n  Top priority - submit times for:")
        for o in na_opps[:5]:
            print(f"    - {o.track_name} ({o.vehicle}/{o.category}/{o.laps})")

    if ranked_opps:
        print(f"\n  Best efficiency improvements:")
        for o in ranked_opps[:5]:
            t = o.tiers[0]
            print(
                f"    - {o.track_name} ({o.vehicle}/{o.category}/{o.laps}): "
                f"rank {o.current_rank} -> {t.target_rank}, "
                f"need {format_time(t.time_delta_cs)} faster, "
                f"AF -{t.af_improvement:.4f}"
            )

    print(f"\n  HTML report: {os.path.abspath(html_path)}")
    print(f"  JSON report: {os.path.abspath(json_path)}")


if __name__ == "__main__":
    main()
