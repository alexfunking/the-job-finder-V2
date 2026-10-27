"""Location matching shared by the ATS probe, the labeling sampler and (next) the deterministic filter."""

from __future__ import annotations

import re
from typing import Final

ISRAEL_KEYWORDS: Final[tuple[str, ...]] = (
    "israel", "tel aviv", "tel-aviv", "tlv", "jaffa", "yafo", "herzliya", "glilot", "haifa",
    "jerusalem", "petah tikva", "petach tikva", "petah-tikva", "petach-tikva", "ramat gan",
    "ramat hahayal", "ramat hachayal", "raanana", "ra'anana", "rehovot", "rishon", "netanya",
    "beer sheva", "be'er sheva", "yokneam", "hod hasharon", "kfar saba", "airport city",
    "or yehuda", "caesarea", "modiin", "givatayim", "bnei brak", "holon", "lod", "ness ziona",
    "rosh haayin", "rosh ha'ayin", "ashdod", "nazareth", "kiryat",
    # Seen in LinkedIn alerts (2026-10-28), which name smaller towns and districts than ATS boards do.
    "yehud", "ramat hasharon", "beer yaakov", "be'er ya'akov", "yakum", "yaqum", "bat yam", "hadera",
    "ashkelon", "beit shemesh", "modi'in", "shoham", "even yehuda", "kfar yona", "karmiel", "afula",
    "tirat carmel", "migdal haemek", "rosh pina", "sderot", "dimona", "ofakim", "omer", "mevaseret",
    "center district", "central district", "northern district", "southern district",
)
# Whole words only: a plain substring check would match "lod" inside "Lodz" or "holon" inside "Holonet".
ISRAEL_PATTERN: Final = re.compile(
    r"\b(?:" + "|".join(re.escape(keyword) for keyword in ISRAEL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def is_israel_location(location: str) -> bool:
    return ISRAEL_PATTERN.search(location) is not None
