"""Job sources: one module per public ATS API, each converting its payload into models.Job."""

from jobfinder.sources.base import Source, SourceResult

__all__ = ["Source", "SourceResult"]
