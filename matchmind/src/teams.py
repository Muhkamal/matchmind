"""Canonical team identities across data sources.

Sources spell clubs differently ("Man City" in football-data.co.uk CSVs,
"Manchester City FC" in football-data.org). Every team entering the model or the
ledger is mapped to one canonical slug here. Unknown names are a hard error,
never a silent "average team".

Matching order for ``canon(name, source)``:
  1. exact alias for that source (case-insensitive)
  2. normalised match against ANY alias (drops "FC"/"AFC", punctuation, "&"->"and")
Anything else raises ``UnknownTeamError``.

Coverage: every club that appears in the football-data.co.uk E0 files since
2008-09 (43 clubs). To add a league, add its clubs here and to ``LEAGUE_TEAMS``.
Run ``python check_teams.py`` after a promotion/relegation season: it asks
football-data.org for the current club list and reports anything unmapped.
"""
from __future__ import annotations

import re

# slug: (display, [football-data.co.uk names], [football-data.org names])
_RAW = {
    "arsenal":          ("Arsenal",          ["Arsenal"],          ["Arsenal FC"]),
    "aston-villa":      ("Aston Villa",      ["Aston Villa"],      ["Aston Villa FC"]),
    "birmingham":       ("Birmingham",       ["Birmingham"],       ["Birmingham City FC"]),
    "blackburn":        ("Blackburn",        ["Blackburn"],        ["Blackburn Rovers FC"]),
    "blackpool":        ("Blackpool",        ["Blackpool"],        ["Blackpool FC"]),
    "bolton":           ("Bolton",           ["Bolton"],           ["Bolton Wanderers FC"]),
    "bournemouth":      ("Bournemouth",      ["Bournemouth"],      ["AFC Bournemouth"]),
    "brentford":        ("Brentford",        ["Brentford"],        ["Brentford FC"]),
    "brighton":         ("Brighton",         ["Brighton"],         ["Brighton & Hove Albion FC"]),
    "burnley":          ("Burnley",          ["Burnley"],          ["Burnley FC"]),
    "cardiff":          ("Cardiff",          ["Cardiff"],          ["Cardiff City FC"]),
    "chelsea":          ("Chelsea",          ["Chelsea"],          ["Chelsea FC"]),
    "coventry":         ("Coventry",         ["Coventry"],         ["Coventry City FC"]),
    "crystal-palace":   ("Crystal Palace",   ["Crystal Palace"],   ["Crystal Palace FC"]),
    "everton":          ("Everton",          ["Everton"],          ["Everton FC"]),
    "fulham":           ("Fulham",           ["Fulham"],           ["Fulham FC"]),
    "huddersfield":     ("Huddersfield",     ["Huddersfield"],     ["Huddersfield Town AFC"]),
    "hull-city":        ("Hull City",        ["Hull"],             ["Hull City AFC"]),
    "ipswich":          ("Ipswich",          ["Ipswich"],          ["Ipswich Town FC"]),
    "leeds":            ("Leeds",            ["Leeds"],            ["Leeds United FC"]),
    "leicester":        ("Leicester",        ["Leicester"],        ["Leicester City FC"]),
    "liverpool":        ("Liverpool",        ["Liverpool"],        ["Liverpool FC"]),
    "luton":            ("Luton",            ["Luton"],            ["Luton Town FC"]),
    "man-city":         ("Man City",         ["Man City"],         ["Manchester City FC"]),
    "man-united":       ("Man United",       ["Man United"],       ["Manchester United FC"]),
    "middlesbrough":    ("Middlesbrough",    ["Middlesbrough"],    ["Middlesbrough FC"]),
    "newcastle":        ("Newcastle",        ["Newcastle"],        ["Newcastle United FC"]),
    "norwich":          ("Norwich",          ["Norwich"],          ["Norwich City FC"]),
    "nottm-forest":     ("Nott'm Forest",    ["Nott'm Forest"],    ["Nottingham Forest FC"]),
    "portsmouth":       ("Portsmouth",       ["Portsmouth"],       ["Portsmouth FC"]),
    "qpr":              ("QPR",              ["QPR"],              ["Queens Park Rangers FC"]),
    "reading":          ("Reading",          ["Reading"],          ["Reading FC"]),
    "sheffield-united": ("Sheffield United", ["Sheffield United"], ["Sheffield United FC"]),
    "southampton":      ("Southampton",      ["Southampton"],      ["Southampton FC"]),
    "stoke":            ("Stoke",            ["Stoke"],            ["Stoke City FC"]),
    "sunderland":       ("Sunderland",       ["Sunderland"],       ["Sunderland AFC"]),
    "swansea":          ("Swansea",          ["Swansea"],          ["Swansea City AFC"]),
    "tottenham":        ("Tottenham",        ["Tottenham"],        ["Tottenham Hotspur FC"]),
    "watford":          ("Watford",          ["Watford"],          ["Watford FC"]),
    "west-brom":        ("West Brom",        ["West Brom"],        ["West Bromwich Albion FC"]),
    "west-ham":         ("West Ham",         ["West Ham"],         ["West Ham United FC"]),
    "wigan":            ("Wigan",            ["Wigan"],            ["Wigan Athletic FC"]),
    "wolves":           ("Wolves",           ["Wolves"],           ["Wolverhampton Wanderers FC"]),
}

TEAMS = {slug: {"display": d, "csv": list(c), "fdo": list(f)} for slug, (d, c, f) in _RAW.items()}

# Leagues the registry can serve. Anything else must be added before it is enabled.
LEAGUE_TEAMS = {"E0": set(TEAMS)}

_NOISE = {"fc", "afc", "cf", "the"}


class UnknownTeamError(ValueError):
    pass


def _norm(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[.'’`]", "", s)             # nott'm -> nottm, a.f.c -> afc
    tokens = [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _NOISE]
    return " ".join(tokens)


_EXACT: dict[tuple[str, str], str] = {}
_NORM: dict[str, str] = {}


def _register(key: str, slug: str) -> None:
    existing = _NORM.get(key)
    if existing is not None and existing != slug:
        raise RuntimeError(f"Team alias collision: {key!r} maps to {existing!r} and {slug!r}")
    _NORM[key] = slug


for _slug, _t in TEAMS.items():
    for _a in _t["csv"]:
        _EXACT[("csv", _a.lower())] = _slug
    for _a in _t["fdo"]:
        _EXACT[("fdo", _a.lower())] = _slug
    for _a in [_slug, _t["display"], *_t["csv"], *_t["fdo"]]:
        _register(_norm(_a), _slug)


def known_display_names() -> list[str]:
    return sorted(t["display"] for t in TEAMS.values())


def canon(name: str, source: str | None = None) -> str:
    """Map a source-specific (or display/slug) name to its canonical slug."""
    if not isinstance(name, str) or not name.strip():
        raise UnknownTeamError(f"Empty team name: {name!r}")
    if source is not None:
        hit = _EXACT.get((source, name.strip().lower()))
        if hit:
            return hit
    hit = _NORM.get(_norm(name))
    if hit:
        return hit
    raise UnknownTeamError(
        f"Unknown team {name!r} (source={source!r}). Add it to src/teams.py "
        f"(run check_teams.py to see what football-data.org calls the current clubs).")


def resolve(name: str) -> str:
    """Accept anything a human might type: display name, slug, either source's alias."""
    return canon(name, None)


def split_fixture(tokens: list[str]) -> tuple[str, str]:
    """Split ['Man','City','Aston','Villa'] into two clubs, trying every split point.

    Needed because both clubs can be multi-word. Returns (home_slug, away_slug).
    Raises UnknownTeamError if no split works, or if the text is ambiguous.
    """
    if len(tokens) < 2:
        raise UnknownTeamError("Need two team names.")
    found: list[tuple[str, str]] = []
    for i in range(1, len(tokens)):
        try:
            h, a = resolve(" ".join(tokens[:i])), resolve(" ".join(tokens[i:]))
        except UnknownTeamError:
            continue
        if h != a:
            found.append((h, a))
    if not found:
        raise UnknownTeamError(f"Could not read two clubs from: {' '.join(tokens)!r}")
    if len(set(found)) > 1:
        raise UnknownTeamError(f"Ambiguous team names: {' '.join(tokens)!r}")
    return found[0]


def display(slug: str) -> str:
    return TEAMS[slug]["display"] if slug in TEAMS else slug


def pretty_fixture(home_slug: str, away_slug: str) -> str:
    return f"{display(home_slug)} – {display(away_slug)}"
