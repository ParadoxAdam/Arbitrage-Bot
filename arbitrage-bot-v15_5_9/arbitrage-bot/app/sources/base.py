from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable
from ..models import Listing


class BaseSource(ABC):
    """
    Interface every marketplace adapter implements.

    Rules for implementations:
      - Respect robots.txt and the site's ToS.
      - Use official APIs first; only scrape if explicitly allowed.
      - Throttle requests; use exponential backoff on errors.
      - Never bypass auth, CAPTCHA, or rate limits.
      - Public listings only.
    """

    name: str = "base"

    @abstractmethod
    def fetch(self, query: str, category: str, limit: int = 50) -> Iterable[Listing]:
        """Yield normalized Listing objects from this source."""
        ...
