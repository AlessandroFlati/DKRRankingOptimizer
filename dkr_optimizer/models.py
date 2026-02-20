from dataclasses import dataclass, field


def parse_time(time_str: str) -> int:
    """Parse MM:SS:CC time string to centiseconds integer."""
    parts = time_str.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid time format: {time_str!r}, expected MM:SS:CC")
    minutes, seconds, centiseconds = int(parts[0]), int(parts[1]), int(parts[2])
    return minutes * 6000 + seconds * 100 + centiseconds


def format_time(cs: int) -> str:
    """Format centiseconds integer to MM:SS:CC string."""
    minutes = cs // 6000
    remainder = cs % 6000
    seconds = remainder // 100
    centiseconds = remainder % 100
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


@dataclass
class PlayerProfile:
    username: str
    combined_rank: int
    current_af: float
    country: str


@dataclass
class PlayerTrackTime:
    track_slug: str
    track_name: str
    vehicle: str  # car, hover, plane
    category: str  # standard, shortcut
    laps: str  # 3-laps, 1-lap
    time_cs: int  # centiseconds, 0 if N/A
    rank: int  # 0 if N/A
    is_na: bool


@dataclass
class LeaderboardEntry:
    rank: int
    username: str
    display_name: str
    time_cs: int
    is_default: bool  # "Default Time" placeholder


@dataclass
class CombinedRankingEntry:
    rank: int
    username: str
    display_name: str
    af: float
    gap: float


@dataclass
class Opportunity:
    track_slug: str
    track_name: str
    vehicle: str
    category: str
    laps: str
    current_rank: int
    current_time_cs: int  # 0 for N/A tracks
    is_na: bool
    # Tiers: list of (target_rank, target_time_cs, positions_gained, af_improvement, time_delta_cs)
    tiers: list = field(default_factory=list)
    # Best efficiency across tiers (af_improvement / time_delta_cs), higher = better
    best_efficiency: float = 0.0
    # Best tier index
    best_tier_idx: int = 0

    @property
    def leaderboard_url(self) -> str:
        return f"https://www.dkr64.com/tracks/{self.track_slug}/{self.vehicle}/{self.category}/{self.laps}"


@dataclass
class OpportunityTier:
    target_rank: int
    opponent_time_cs: int  # the opponent's actual time
    target_time_cs: int  # time you must achieve to surpass (opponent - 1cs)
    positions_gained: int
    af_improvement: float
    time_delta_cs: int  # time improvement needed: your_time - target_time (includes surpass margin)
    efficiency: float  # af_improvement / time_delta_cs


@dataclass
class OvertakePlanItem:
    track_slug: str
    track_name: str
    vehicle: str
    category: str
    laps: str
    is_na: bool
    current_rank: int
    current_time_cs: int
    new_rank: int
    target_time_cs: int
    opponent_time_cs: int
    positions_gained: int
    af_improvement: float
    time_delta_cs: int
    efficiency: float

    @property
    def leaderboard_url(self) -> str:
        return f"https://www.dkr64.com/tracks/{self.track_slug}/{self.vehicle}/{self.category}/{self.laps}"


@dataclass
class OvertakePlan:
    target_username: str
    target_af: float
    current_af: float
    af_gap: float
    total_positions_needed: int
    total_positions_gained: int
    total_time_investment_cs: int  # only ranked tracks (N/A tracks excluded)
    new_af: float
    items: list = field(default_factory=list)  # list[OvertakePlanItem]
    feasible: bool = True
