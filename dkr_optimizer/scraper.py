import hashlib
import json
import os
import time

import requests

BASE_URL = "https://www.dkr64.com"


class DKRScraper:
    def __init__(self, cache_dir: str = "cache", cache_ttl_hours: float = 24,
                 request_delay: float = 0.5):
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_hours * 3600
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self._session_established = False
        self._last_request_time = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def _establish_session(self):
        """Make a throwaway request to obtain the ci_session cookie."""
        if self._session_established:
            return
        self.session.get(BASE_URL, timeout=15)
        self._session_established = True
        self._throttle()

    def _throttle(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def _cache_path(self, url: str) -> str:
        return os.path.join(self.cache_dir, self._cache_key(url) + ".html")

    def _meta_path(self, url: str) -> str:
        return os.path.join(self.cache_dir, self._cache_key(url) + ".meta")

    def _is_cache_valid(self, url: str) -> bool:
        meta_path = self._meta_path(url)
        if not os.path.exists(meta_path):
            return False
        with open(meta_path, "r") as f:
            meta = json.load(f)
        age = time.time() - meta["fetched_at"]
        return age < self.cache_ttl_seconds

    def _read_cache(self, url: str) -> str | None:
        """Read cached content. Returns None if cached as non-existent (404/500)."""
        cache_path = self._cache_path(url)
        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read()
        if content == "__DKR_NOT_FOUND__":
            return None
        return content

    def _write_cache(self, url: str, html: str):
        cache_path = self._cache_path(url)
        meta_path = self._meta_path(url)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(html)
        with open(meta_path, "w") as f:
            json.dump({"url": url, "fetched_at": time.time()}, f)

    def fetch(self, url: str) -> str | None:
        """Fetch a URL, returning HTML string. Uses cache if valid.

        Returns None for pages that don't exist (cached 404/500).
        Raises on unexpected errors.
        """
        if self._is_cache_valid(url):
            return self._read_cache(url)

        self._establish_session()
        self._throttle()

        response = self.session.get(url, timeout=30)

        if response.status_code in (404, 500):
            # Non-existent leaderboard (e.g., car on a hovercraft-only track)
            self._write_cache(url, "__DKR_NOT_FOUND__")
            return None

        response.raise_for_status()

        html = response.text
        if not html:
            raise RuntimeError(
                f"Empty response from {url} (status {response.status_code}). "
                f"Session cookie may have expired."
            )

        self._write_cache(url, html)
        return html

    def clear_cache(self):
        """Remove all cached files."""
        for filename in os.listdir(self.cache_dir):
            filepath = os.path.join(self.cache_dir, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)

    def player_url(self, username: str) -> str:
        return f"{BASE_URL}/players/{username}"

    def combined_ranking_url(self) -> str:
        return f"{BASE_URL}/average-finish/combined/combined"

    def leaderboard_url(self, track_slug: str, vehicle: str, category: str,
                        laps: str) -> str:
        return f"{BASE_URL}/tracks/{track_slug}/{vehicle}/{category}/{laps}"
