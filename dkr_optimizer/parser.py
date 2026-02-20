import re

from bs4 import BeautifulSoup

from dkr_optimizer.models import (
    CombinedRankingEntry,
    LeaderboardEntry,
    PlayerProfile,
    PlayerTrackTime,
    parse_time,
)

# Column order for the 6 time cells in each row on the player page
CELL_MAPPING = [
    ("car", "3-laps"),
    ("car", "1-lap"),
    ("hover", "3-laps"),
    ("hover", "1-lap"),
    ("plane", "3-laps"),
    ("plane", "1-lap"),
]

TIME_PATTERN = re.compile(r"\d{2}:\d{2}:\d{2}")


def parse_player_page(html: str) -> tuple[PlayerProfile, list[PlayerTrackTime]]:
    """Parse a player profile page, returning profile info and all track times."""
    soup = BeautifulSoup(html, "lxml")

    # Player profile
    rank_el = soup.select_one("div.player-name strong.text-primary")
    rank_text = rank_el.text.strip().lstrip("#")
    combined_rank = int(rank_text)

    breadcrumb = soup.select_one("ol.breadcrumb li.active")
    username = breadcrumb.text.strip()

    country_el = soup.select_one("div.player-country span.flag-icon")
    country = ""
    if country_el:
        for cls in country_el.get("class", []):
            if cls.startswith("flag-icon-") and cls != "flag-icon":
                country = cls.replace("flag-icon-", "")
                break

    # Get AF from statistics tab if available
    current_af = 0.0
    standard_tab = soup.select_one("#standard")
    if standard_tab:
        for row in standard_tab.select("tbody tr"):
            text = row.get_text()
            if "Average Finish" in text and "Combined" in text:
                af_match = re.search(r"(\d+\.\d+)", row.get_text())
                if af_match:
                    current_af = float(af_match.group(1))
                    break

    profile = PlayerProfile(
        username=username,
        combined_rank=combined_rank,
        current_af=current_af,
        country=country,
    )

    # Parse all track times from the #times tab
    times_tab = soup.select_one("#times")
    area_tables = times_tab.select("table.table-times")

    track_times = []

    for table in area_tables:
        rows = table.select("tbody tr")
        i = 0
        while i < len(rows):
            row = rows[i]
            track_el = row.select_one("h3.h4")
            if not track_el:
                i += 1
                continue

            track_name = track_el.text.strip()
            track_link = row.select_one("td.track-image-td a")
            track_slug = track_link["href"].rstrip("/").split("/")[-1]

            # Standard row (current) and shortcut row (next)
            for cat_idx in range(2):
                cat_row = rows[i + cat_idx]
                category = "standard" if cat_idx == 0 else "shortcut"
                time_cells = cat_row.select("td.times-td-border-left")

                for cell_idx, cell in enumerate(time_cells):
                    vehicle, laps = CELL_MAPPING[cell_idx]
                    is_na = "text-muted" in cell.get("class", [])

                    time_cs = 0
                    rank = 0

                    if not is_na:
                        a_tag = cell.select_one("a")
                        if a_tag:
                            time_str = a_tag.text.strip()
                            time_cs = parse_time(time_str)

                            # Extract rank from popover
                            popover = cell.select_one(".popover-body")
                            if popover:
                                for strong in popover.select("strong"):
                                    if "Rank" in strong.text:
                                        rank_span = strong.find_next("span")
                                        if rank_span:
                                            rank_text = rank_span.text.strip()
                                            if rank_text.isdigit():
                                                rank = int(rank_text)
                                        break

                    track_times.append(PlayerTrackTime(
                        track_slug=track_slug,
                        track_name=track_name,
                        vehicle=vehicle,
                        category=category,
                        laps=laps,
                        time_cs=time_cs,
                        rank=rank,
                        is_na=is_na,
                    ))

            i += 2

    return profile, track_times


def parse_leaderboard(html: str) -> list[LeaderboardEntry]:
    """Parse a track leaderboard page, returning all entries."""
    soup = BeautifulSoup(html, "lxml")

    table = soup.select_one("table.table-striped")
    if not table:
        return []

    rows = table.select("tbody tr")
    entries = []
    prev_rank = 0

    for row in rows:
        rank_th = row.select_one("th.id-field")
        rank_text = rank_th.get_text(strip=True) if rank_th else ""
        if rank_text.isdigit():
            rank = int(rank_text)
            prev_rank = rank
        else:
            rank = prev_rank  # tied

        player_a = row.select_one("a.reset-link-color")
        if not player_a:
            continue
        username = player_a["href"].rstrip("/").split("/players/")[-1]
        display_name = player_a.text.strip()

        # Extract time
        time_tds = row.select("td.time-field")
        if not time_tds:
            continue
        time_cell = time_tds[0]

        # Check for default time
        default_icon = time_cell.select_one('i.fa-info[title="Default Time"]')
        is_default = default_icon is not None

        # Get time value
        strong = time_cell.select_one("strong.top-time")
        if strong:
            time_str = strong.text.strip()
        else:
            time_str = _extract_time_from_cell(time_cell)

        if not time_str:
            continue

        time_cs = parse_time(time_str)

        entries.append(LeaderboardEntry(
            rank=rank,
            username=username,
            display_name=display_name,
            time_cs=time_cs,
            is_default=is_default,
        ))

    return entries


def parse_combined_ranking(html: str) -> list[CombinedRankingEntry]:
    """Parse the combined average-finish ranking page."""
    soup = BeautifulSoup(html, "lxml")

    table = soup.select_one("table.table-striped")
    if not table:
        return []

    rows = table.select("tbody tr")
    entries = []
    prev_rank = 0

    for row in rows:
        rank_th = row.select_one("th.id-field")
        rank_text = rank_th.get_text(strip=True) if rank_th else ""
        if rank_text.isdigit():
            rank = int(rank_text)
            prev_rank = rank
        else:
            rank = prev_rank

        player_a = row.select_one("a.reset-link-color")
        if not player_a:
            continue
        username = player_a["href"].rstrip("/").split("/players/")[-1]
        display_name = player_a.text.strip()

        time_tds = row.select("td.time-field")
        if not time_tds:
            continue

        af_text = time_tds[0].get_text(strip=True)
        try:
            af = float(af_text)
        except ValueError:
            continue

        gap = 0.0
        if len(time_tds) > 1:
            gap_text = time_tds[1].get_text(strip=True)
            gap_match = re.search(r"[\+\-]?\d+\.\d+", gap_text.replace(",", ""))
            if gap_match:
                gap = float(gap_match.group())

        entries.append(CombinedRankingEntry(
            rank=rank,
            username=username,
            display_name=display_name,
            af=af,
            gap=gap,
        ))

    return entries


def _extract_time_from_cell(cell) -> str:
    """Extract a time string (MM:SS:CC) from a td cell's text content."""
    for child in cell.children:
        if isinstance(child, str):
            match = TIME_PATTERN.search(child)
            if match:
                return match.group()
    # Fallback: search entire text
    match = TIME_PATTERN.search(cell.get_text())
    if match:
        return match.group()
    return ""
