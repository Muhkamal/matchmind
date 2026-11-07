"""Deterministic "why this pick" explanations.

Every number comes from the model itself. The template renderer is the default and cannot
invent facts. An optional LLM "polish" step (OPENAI_API_KEY) may rephrase the facts, but its
output is VALIDATED: every number it contains must already be in the facts, and both team
names must appear. Anything else falls back to the template.

Parameter convention (see models.py): dfn is defence LEAKINESS. Higher = concedes more.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

import numpy as np

from . import teams
from .models import _shrunk

_NUM = re.compile(r"\d+(?:\.\d+)?")


def explain_match(model, home: str, away: str, full: dict | None = None,
                  profile_h: dict | None = None, profile_a: dict | None = None) -> dict:
    full = full or model.predict_full(home, away)
    p, p_elo, p_dc, matrix = full["p"], full["p_elo"], full["p_dc"], full["matrix"]
    top_pick = ["H", "D", "A"][int(p.argmax())]
    hn, an = teams.display(home), teams.display(away)

    elo_gap = model.elo.ratings.get(home, model.elo.start) + model.elo.home_adv \
        - model.elo.ratings.get(away, model.elo.start)
    atk_h, dfn_h = _shrunk(model.dc, home)
    atk_a, dfn_a = _shrunk(model.dc, away)
    idx = np.arange(matrix.shape[0])
    xg_h = float((matrix.sum(axis=1) * idx).sum())
    xg_a = float((matrix.sum(axis=0) * idx).sum())

    # Net scoring edge exactly as the model builds expected goals (home advantage excluded):
    # home scores more when atk_h + dfn_a is large; away when atk_a + dfn_h is large.
    edge = (atk_h + dfn_a) - (atk_a + dfn_h)

    factors = [
        {"factor": "Elo strength gap", "value": round(elo_gap, 1),
         "detail": f"{hn} (with home advantage) is {abs(elo_gap):.0f} Elo "
                   f"{'above' if elo_gap > 0 else 'below'} {an}.",
         "direction": "home" if elo_gap > 0 else "away"},
        {"factor": "Attack vs defence", "value": round(edge, 3),
         "detail": f"{hn} attack {atk_h:+.2f} (higher = scores more) against {an} defence "
                   f"{dfn_a:+.2f} (higher = concedes more); {an} attack {atk_a:+.2f} against "
                   f"{hn} defence {dfn_h:+.2f}.",
         "direction": "home" if edge > 0 else "away"},
        {"factor": "Expected goals", "value": [round(xg_h, 2), round(xg_a, 2)],
         "detail": f"Model expects about {xg_h:.1f} goals for {hn} and {xg_a:.1f} for {an}.",
         "direction": "home" if xg_h > xg_a else "away"},
    ]
    for team, name, prof in ((home, hn, profile_h), (away, an, profile_a)):
        if prof and prof.get("n", 0) and prof["n"] < 12:
            factors.append({"factor": f"Thin history: {name}", "value": prof["n"],
                            "detail": f"{name} has only {prof['n']} matches in our data, so "
                                      f"its ratings are pulled toward the league average.",
                            "direction": "uncertain"})
        elif prof and prof.get("last5"):
            factors.append({"factor": f"{name} form (last 5)", "value": prof["last5"],
                            "detail": f"{name} form, most recent first: {prof['last5']}.",
                            "direction": "form"})

    disagreement = None
    if p_elo.argmax() != p_dc.argmax():
        disagreement = (f"Elo leans {['H', 'D', 'A'][p_elo.argmax()]} ({p_elo.max():.0%}) but "
                        f"Dixon-Coles leans {['H', 'D', 'A'][p_dc.argmax()]} ({p_dc.max():.0%}); "
                        f"the two engines disagree, so treat the pick with extra caution.")

    return {
        "home": home, "away": away, "home_name": hn, "away_name": an,
        "p_home": float(p[0]), "p_draw": float(p[1]), "p_away": float(p[2]),
        "top_pick": top_pick,
        "confidence": "high" if p.max() >= 0.60 else ("medium" if p.max() >= 0.50 else "low"),
        "factors": factors, "engine_disagreement": disagreement,
        "markets": {k: full["markets"][k] for k in ("p_over25", "p_btts")},
    }


def render_text(expl: dict) -> str:
    """Template renderer: the default, free, cannot-invent-facts output."""
    lines = [
        f"{expl['home_name']} vs {expl['away_name']}: "
        f"{expl['p_home']:.0%} / {expl['p_draw']:.0%} / {expl['p_away']:.0%} "
        f"(home / draw / away). Top pick {expl['top_pick']} ({expl['confidence']} confidence).",
        "",
    ]
    lines += [f"• {f['detail']}" for f in expl["factors"]]
    if expl["engine_disagreement"]:
        lines += ["", f"⚠ {expl['engine_disagreement']}"]
    return "\n".join(lines)


def validate_polish(facts: str, output: str, names: list[str]) -> bool:
    """True only if the rewrite adds no numbers, keeps every team name, and stays short."""
    if not output or len(output) > 900:
        return False
    allowed = set(_NUM.findall(facts))
    if not set(_NUM.findall(output)) <= allowed:
        return False
    low = output.lower()
    return all(n.lower() in low for n in names)


def polish(expl: dict, timeout: float = 10.0) -> str:
    """Optional LLM rephrase. Falls back to the template on any problem."""
    facts = render_text(expl)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return facts
    prompt = ("Rewrite this football prediction explanation in 2-4 natural sentences. You may "
              "NOT change, add or remove any number, team name or fact. Keep any warning.\n\n"
              + facts)
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                             "messages": [{"role": "user", "content": prompt}],
                             "temperature": 0.2}).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        text = out["choices"][0]["message"]["content"].strip()
    except Exception:
        return facts
    return text if validate_polish(facts, text, [expl["home_name"], expl["away_name"]]) else facts
