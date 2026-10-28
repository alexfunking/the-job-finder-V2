"""The order in which jobs are offered for labeling.

A plain random sample of the store would be mostly jobs outside Israel or with senior titles, which are
trivially "not fit" and would make any filter look good, and would leave too few "fit" labels to measure
recall. Jobs are split into strata and drawn by fixed shares instead, so the eval set is mostly the jobs
the scorer will really have to decide on: email-alert jobs (nearly all junior / graduate roles, and
title-only, which the extractor must handle too) and board jobs the pre-filter keeps. The jobs the filter
rejects are still sampled, so the eval set can catch the filter's mistakes.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Final

from jobfinder.filtering import prefilter
from jobfinder.geo import is_israel_location
from jobfinder.labels import Label
from jobfinder.models import Job


class Stratum(StrEnum):
    ALERT = "alert"
    FILTER_KEPT = "filter_kept"
    ISRAEL_FILTER_REJECTED = "israel_filter_rejected"
    OUTSIDE_ISRAEL = "outside_israel"


SHARES: Final[Mapping[Stratum, float]] = {
    Stratum.ALERT: 0.3,
    Stratum.FILTER_KEPT: 0.4,
    Stratum.ISRAEL_FILTER_REJECTED: 0.2,
    Stratum.OUTSIDE_ISRAEL: 0.1,
}


def stratum_of(job: Job) -> Stratum:
    # A job without a description came from an email alert, whatever the filter says about it.
    if job.description_text is None:
        return Stratum.ALERT
    if prefilter(job).kept:
        return Stratum.FILTER_KEPT
    if is_israel_location(job.location):
        return Stratum.ISRAEL_FILTER_REJECTED
    return Stratum.OUTSIDE_ISRAEL


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
