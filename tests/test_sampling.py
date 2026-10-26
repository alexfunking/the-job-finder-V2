from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest

from jobfinder.labels import Label
from jobfinder.models import Job
from jobfinder.sampling import Stratum, labeling_queue, stratum_of

T1 = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def job(job_id: str, *, title: str = "Backend Engineer", location: str = "Tel Aviv, Israel") -> Job:
    return Job(
        source="greenhouse",
        slug="acme",
        job_id=job_id,
        company="Acme",
        title=title,
        location=location,
        url=f"https://example.test/acme/{job_id}",
        description_text="Build things.",
    )


def pool(israel: int = 70, senior: int = 20, outside: int = 10) -> list[Job]:
    return (
        [job(f"il{i}") for i in range(israel)]
        + [job(f"sr{i}", title="Senior Backend Engineer") for i in range(senior)]
        + [job(f"us{i}", location="New York, NY") for i in range(outside)]
    )


def labels_for(jobs: list[Job], verdict: str = "fit") -> dict[str, Label]:
    return {
        j.key: Label.model_validate({"verdict": verdict, "reason": "r", "labeled_at": T1, "job": j}) for j in jobs
    }


@pytest.mark.parametrize(
    ("title", "location", "expected"),
    [
        ("Backend Engineer", "Tel Aviv, Israel", Stratum.ISRAEL),
        ("Product Manager", "Herzliya", Stratum.ISRAEL),
        ("Senior Backend Engineer", "Tel Aviv", Stratum.ISRAEL_SENIOR_TITLE),
        ("Sr. Data Scientist", "Israel", Stratum.ISRAEL_SENIOR_TITLE),
        ("Team Lead, Platform", "Haifa", Stratum.ISRAEL_SENIOR_TITLE),
        ("Leading Edge Intern", "Haifa", Stratum.ISRAEL),  # "Leading" is not "Lead"
        ("Backend Engineer", "Lodz, Poland", Stratum.OUTSIDE_ISRAEL),
        ("Senior Backend Engineer", "Remote - US", Stratum.OUTSIDE_ISRAEL),
        ("Backend Engineer", "", Stratum.OUTSIDE_ISRAEL),
    ],
)
def test_stratum_of(title: str, location: str, expected: Stratum) -> None:
    assert stratum_of(job("1", title=title, location=location)) is expected


def test_queue_follows_the_shares_from_the_start() -> None:
    queue = labeling_queue(pool(), {}, seed=1)
    first_ten = Counter(stratum_of(j) for j in queue[:10])
    assert first_ten == {Stratum.ISRAEL: 7, Stratum.ISRAEL_SENIOR_TITLE: 2, Stratum.OUTSIDE_ISRAEL: 1}


def test_queue_contains_every_unlabeled_candidate_once() -> None:
    jobs = pool()
    labeled = labels_for(jobs[:5])
    queue = labeling_queue(jobs, labeled, seed=1)
    assert len(queue) == len({j.key for j in queue}) == len(jobs) - 5
    assert not {j.key for j in queue} & labeled.keys()


def test_same_seed_same_order_and_different_seed_different_order() -> None:
    jobs = pool()
    assert labeling_queue(jobs, {}, seed=7) == labeling_queue(list(reversed(jobs)), {}, seed=7)
    assert labeling_queue(jobs, {}, seed=7) != labeling_queue(jobs, {}, seed=8)


def test_existing_labels_count_toward_the_shares() -> None:
    jobs = pool()
    # A previous session labeled 7 Israel jobs and nothing else: the next picks catch up on the other strata.
    labeled = labels_for(jobs[:7])
    queue = labeling_queue(jobs, labeled, seed=1)
    assert {stratum_of(j) for j in queue[:3]} == {Stratum.ISRAEL_SENIOR_TITLE, Stratum.OUTSIDE_ISRAEL}


def test_skipped_jobs_are_not_offered_again_but_do_not_count_toward_the_shares() -> None:
    jobs = pool()
    skipped = labels_for(jobs[:7], verdict="skip")
    queue = labeling_queue(jobs, skipped, seed=1)
    assert not {j.key for j in queue} & skipped.keys()
    assert stratum_of(queue[0]) is Stratum.ISRAEL


def test_an_exhausted_stratum_gives_way_to_the_others() -> None:
    queue = labeling_queue(pool(israel=3, senior=20, outside=0), {}, seed=1)
    assert [stratum_of(j) for j in queue[:4]] == [
        Stratum.ISRAEL, Stratum.ISRAEL_SENIOR_TITLE, Stratum.ISRAEL, Stratum.ISRAEL,
    ]
    assert len(queue) == 23
    assert all(stratum_of(j) is Stratum.ISRAEL_SENIOR_TITLE for j in queue[4:])


def test_strata_missing_from_shares_are_not_offered() -> None:
    queue = labeling_queue(pool(), {}, seed=1, shares={Stratum.ISRAEL: 1.0})
    assert len(queue) == 70


def test_shares_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        labeling_queue(pool(), {}, seed=1, shares={Stratum.ISRAEL: 1.0, Stratum.OUTSIDE_ISRAEL: 0})
