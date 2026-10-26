"""The order in which jobs are offered for labeling.

A plain random sample of the store would be mostly jobs outside Israel, which are trivially "not fit" and
would make any filter look good. Jobs are split into rough strata and drawn by fixed shares instead, so
the eval set is mostly the jobs the scorer will really have to decide on. The strata only set how often
each kind of job is shown; every stratum is still sampled, so the eval set can catch the filter's mistakes.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Final

from jobfinder.geo import is_israel_location
from jobfinder.labels import Label
from jobfinder.models import Job


class Stratum(StrEnum):
    ISRAEL = "israel"
    ISRAEL_SENIOR_TITLE = "israel_senior_title"
    OUTSIDE_ISRAEL = "outside_israel"


# Rough on purpose: deciding seniority properly is the filter's job, not the sampler's.
SENIOR_TITLE: Final = re.compile(
    r"\b(?:senior|sr|lead|principal|staff|head|director|vp|chief)\b", re.IGNORECASE
)

SHARES: Final[Mapping[Stratum, float]] = {
    Stratum.ISRAEL: 0.7,
    Stratum.ISRAEL_SENIOR_TITLE: 0.2,
    Stratum.OUTSIDE_ISRAEL: 0.1,
}


def stratum_of(job: Job) -> Stratum:
    if not is_israel_location(job.location):
        return Stratum.OUTSIDE_ISRAEL
    if SENIOR_TITLE.search(job.title):
        return Stratum.ISRAEL_SENIOR_TITLE
    return Stratum.ISRAEL


def labeling_queue(
    candidates: Iterable[Job],
    labeled: Mapping[str, Label],
    seed: int,
    shares: Mapping[Stratum, float] = SHARES,
) -> list[Job]:
    """Unlabeled candidates in the order to offer them, reproducible for a given seed and store.

    Each next job comes from the stratum furthest below its share, counting the labels already in the
    eval set, so the shares hold across sessions and when jobs are skipped.
    A stratum missing from `shares` is not offered at all.
    """
    if any(share <= 0 for share in shares.values()):
        raise ValueError("every stratum share must be positive")
    rng = random.Random(seed)
    buckets: dict[Stratum, list[Job]] = {stratum: [] for stratum in shares}
    for job in sorted(candidates, key=lambda j: j.key):
        stratum = stratum_of(job)
        if job.key not in labeled and stratum in buckets:
            buckets[stratum].append(job)
    for stratum in shares:
        rng.shuffle(buckets[stratum])

    taken = {stratum: 0 for stratum in shares}
    for label in labeled.values():
        stratum = stratum_of(label.job)
        if label.counts and stratum in taken:
            taken[stratum] += 1

    queue: list[Job] = []
    while True:
        open_strata = [stratum for stratum in shares if buckets[stratum]]
        if not open_strata:
            return queue
        # Ties go to the first stratum in `shares`.
        stratum = min(open_strata, key=lambda s: taken[s] / shares[s])
        queue.append(buckets[stratum].pop())
        taken[stratum] += 1
