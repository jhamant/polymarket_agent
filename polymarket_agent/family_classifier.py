from __future__ import annotations

import re


SPECIALIST_KEYWORDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "regulatory_legal",
        "Regulatory and legal markets",
        (
            "convicted",
            "sentence",
            "sentenced",
            "court",
            "judge",
            "lawsuit",
            "trial",
            "approval",
            "approved",
            "denied",
            "regulator",
            "regulatory",
            "sec",
            "fda",
            "ftc",
            "doj",
            "commission",
        ),
    ),
    (
        "macro_releases",
        "Macroeconomic release markets",
        (
            "cpi",
            "pce",
            "gdp",
            "payroll",
            "jobs",
            "unemployment",
            "inflation",
            "fed",
            "fomc",
            "treasury",
            "rate cut",
            "rate hike",
            "bea",
            "bls",
        ),
    ),
    (
        "weather_disaster",
        "Weather and natural disaster markets",
        (
            "hurricane",
            "storm",
            "rainfall",
            "snowfall",
            "temperature",
            "weather",
            "landfall",
            "heatwave",
            "tornado",
            "el nino",
            "la nina",
        ),
    ),
    (
        "sports_official_data",
        "Sports markets with official data",
        (
            "nba",
            "nhl",
            "mlb",
            "nfl",
            "fifa",
            "world cup",
            "stanley cup",
            "finals",
            "qualify",
            "qualifier",
            "super bowl",
            "champions league",
            "premier league",
            "la liga",
            "serie a",
            "bundesliga",
            "playoffs",
            "match",
            "tournament",
        ),
    ),
)


def build_text_haystack(*parts: str) -> str:
    return " ".join(part.lower() for part in parts if part)


def classify_market_family(*parts: str) -> tuple[str, str, list[str]]:
    haystack = build_text_haystack(*parts)
    ranked: list[tuple[int, str, str, list[str]]] = []
    for family_key, family_label, keywords in SPECIALIST_KEYWORDS:
        matched = sorted(
            {keyword for keyword in keywords if re.search(rf"\b{re.escape(keyword)}\b", haystack)}
        )
        ranked.append((len(matched), family_key, family_label, matched))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, family_key, family_label, matched = ranked[0]
    if best_score <= 0:
        return ("misc", "Misc or unclear markets", [])
    return family_key, family_label, matched
