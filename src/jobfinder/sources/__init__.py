"""Job sources: one module per public ATS API, each converting its payload into models.Job."""

from typing import Final

from jobfinder.sources.ashby import AshbySource
from jobfinder.sources.base import Source, SourceResult
from jobfinder.sources.greenhouse import GreenhouseSource

# Every source the scanner can use, by the name that appears in sources.toml and in Job.source.
SOURCES: Final[dict[str, Source]] = {source.name: source for source in (GreenhouseSource(), AshbySource())}

__all__ = ["SOURCES", "Source", "SourceResult"]
