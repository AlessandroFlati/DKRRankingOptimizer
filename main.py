import argparse
import os
import sys

import yaml

from dkr_optimizer.models import format_time
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

    # Step 5: Compute opportunities
    print("\nComputing optimization opportunities...")
    opportunities = compute_opportunities(
        player_times=player_times,
        leaderboards=leaderboards,
        total_tracks=valid_tracks,
        player_username=username,
    )

    # Step 6: Compute overtake plans
    overtake_min_time = None
    overtake_min_tracks = None
    if current_rank > 1:
        target_entry = next(
            (r for r in ranking if r.rank == current_rank - 1),
            None,
        )
        if target_entry:
            print(f"\nComputing overtake plans to beat #{target_entry.rank} {target_entry.username} (AF {target_entry.af})...")
            overtake_min_time = compute_overtake_plan(
                player_times=player_times,
                leaderboards=leaderboards,
                current_af=current_af,
                target_af=target_entry.af,
                total_tracks=valid_tracks,
                player_username=username,
                target_username=target_entry.username,
            )
            overtake_min_tracks = compute_overtake_plan_min_tracks(
                player_times=player_times,
                leaderboards=leaderboards,
                current_af=current_af,
                target_af=target_entry.af,
                total_tracks=valid_tracks,
                player_username=username,
                target_username=target_entry.username,
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
