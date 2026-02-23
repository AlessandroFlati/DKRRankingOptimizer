import dataclasses
import json
import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from dkr_optimizer.models import (
    Opportunity,
    OpportunityTier,
    OvertakePlan,
    PlayerProfile,
    format_time,
)


def generate_reports(
    profile: PlayerProfile,
    current_af: float,
    current_rank: int,
    opportunities: list[Opportunity],
    total_tracks: int,
    output_dir: str,
    template_dir: str = "templates",
    overtake_min_time: OvertakePlan | None = None,
    overtake_min_tracks: OvertakePlan | None = None,
    overtake_min_time_plane: OvertakePlan | None = None,
    overtake_min_tracks_plane: OvertakePlan | None = None,
):
    """Generate both HTML and JSON reports."""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    na_opps = [o for o in opportunities if o.is_na]
    ranked_opps = [o for o in opportunities if not o.is_na and o.tiers]
    no_improvement = [o for o in opportunities if not o.is_na and not o.tiers]

    report_data = _build_report_data(
        profile, current_af, current_rank, opportunities,
        na_opps, ranked_opps, no_improvement, total_tracks, timestamp,
        overtake_min_time, overtake_min_tracks,
        overtake_min_time_plane, overtake_min_tracks_plane,
    )

    # JSON report
    json_path = os.path.join(output_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, default=str)

    # HTML report
    # Pre-convert tier dataclasses to dicts so Jinja2 tojson works
    for opp in ranked_opps:
        opp.tiers_json = [dataclasses.asdict(t) for t in opp.tiers]

    env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
    env.filters["format_time"] = format_time
    template = env.get_template("report.html")
    html = template.render(
        profile=profile,
        current_af=current_af,
        current_rank=current_rank,
        na_opps=na_opps,
        ranked_opps=ranked_opps,
        no_improvement=no_improvement,
        total_tracks=total_tracks,
        timestamp=timestamp,
        format_time=format_time,
        float_inf=float("inf"),
        overtake_min_time=overtake_min_time,
        overtake_min_tracks=overtake_min_tracks,
        overtake_min_time_plane=overtake_min_time_plane,
        overtake_min_tracks_plane=overtake_min_tracks_plane,
    )
    html_path = os.path.join(output_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    return html_path, json_path


def _build_report_data(
    profile, current_af, current_rank, opportunities,
    na_opps, ranked_opps, no_improvement, total_tracks, timestamp,
    overtake_min_time=None, overtake_min_tracks=None,
    overtake_min_time_plane=None, overtake_min_tracks_plane=None,
) -> dict:
    """Build JSON-serializable report data."""
    data = {
        "metadata": {
            "generated_at": timestamp,
            "total_tracks_in_scope": total_tracks,
        },
        "player": {
            "username": profile.username,
            "country": profile.country,
            "combined_rank": current_rank,
            "current_af": current_af,
        },
        "summary": {
            "tracks_with_times": len(ranked_opps) + len(no_improvement),
            "tracks_na": len(na_opps),
            "tracks_with_improvement_possible": len(ranked_opps),
            "tracks_at_first_place": len(no_improvement),
        },
        "opportunities": [_opportunity_to_dict(o) for o in opportunities],
    }
    if overtake_min_time:
        data["overtake_min_time"] = _overtake_plan_to_dict(overtake_min_time)
    if overtake_min_tracks:
        data["overtake_min_tracks"] = _overtake_plan_to_dict(overtake_min_tracks)
    if overtake_min_time_plane:
        data["overtake_min_time_plane"] = _overtake_plan_to_dict(overtake_min_time_plane)
    if overtake_min_tracks_plane:
        data["overtake_min_tracks_plane"] = _overtake_plan_to_dict(overtake_min_tracks_plane)
    return data


def _opportunity_to_dict(o: Opportunity) -> dict:
    result = {
        "track_slug": o.track_slug,
        "track_name": o.track_name,
        "vehicle": o.vehicle,
        "category": o.category,
        "laps": o.laps,
        "current_rank": o.current_rank,
        "current_time": format_time(o.current_time_cs) if o.current_time_cs else "N/A",
        "current_time_cs": o.current_time_cs,
        "is_na": o.is_na,
        "leaderboard_url": o.leaderboard_url,
        "best_efficiency": o.best_efficiency if o.best_efficiency != float("inf") else "inf",
        "tiers": [],
    }
    for tier in o.tiers:
        result["tiers"].append({
            "target_rank": tier.target_rank,
            "opponent_time": format_time(tier.opponent_time_cs),
            "opponent_time_cs": tier.opponent_time_cs,
            "target_time": format_time(tier.target_time_cs),
            "target_time_cs": tier.target_time_cs,
            "positions_gained": tier.positions_gained,
            "af_improvement": round(tier.af_improvement, 4),
            "time_delta_cs": tier.time_delta_cs,
            "time_delta": format_time(tier.time_delta_cs),
            "efficiency": tier.efficiency if tier.efficiency != float("inf") else "inf",
        })
    return result


def _overtake_plan_to_dict(plan: OvertakePlan) -> dict:
    return {
        "target_username": plan.target_username,
        "target_af": plan.target_af,
        "current_af": plan.current_af,
        "af_gap": round(plan.af_gap, 4),
        "total_positions_needed": plan.total_positions_needed,
        "total_positions_gained": plan.total_positions_gained,
        "total_time_investment_cs": plan.total_time_investment_cs,
        "total_time_investment": format_time(plan.total_time_investment_cs),
        "new_af": round(plan.new_af, 4),
        "feasible": plan.feasible,
        "items": [
            {
                "track_slug": it.track_slug,
                "track_name": it.track_name,
                "vehicle": it.vehicle,
                "category": it.category,
                "laps": it.laps,
                "is_na": it.is_na,
                "current_rank": it.current_rank,
                "current_time": format_time(it.current_time_cs) if it.current_time_cs else "N/A",
                "new_rank": it.new_rank,
                "target_time": format_time(it.target_time_cs),
                "opponent_time": format_time(it.opponent_time_cs),
                "positions_gained": it.positions_gained,
                "af_improvement": round(it.af_improvement, 4),
                "time_delta_cs": it.time_delta_cs,
                "time_delta": format_time(it.time_delta_cs) if it.time_delta_cs else "N/A",
                "efficiency": it.efficiency if it.efficiency != float("inf") else "inf",
                "leaderboard_url": it.leaderboard_url,
            }
            for it in plan.items
        ],
    }
