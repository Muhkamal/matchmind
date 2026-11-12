# Beyond win/draw/loss: what is possible, and what each needs

Rule for every market: it is only published if it can be stored before kickoff, graded automatically, and compared with a
simple baseline in public. No data to grade it with means it does not ship.

## Done (v1.9/1.10): from goals data you already have
25 match-level markets: Double Chance, goal lines, clean sheets, exact score, Asian handicap half-lines, win to nil,
total-goals bands, team to score 2+/3+, BTTS with Over/Under 2.5. Same ledger, same automatic grading.

## Deliberately not built
Public value-bet and staking features. The model scores worse than closing odds, so most 'edges' would be model error,
and the site's promise is an auditable record, not betting advice. A private research test lives in backtest.py instead.

## Next: team statistics (shots, shots on target, corners, cards)
- Training: football-data.co.uk CSVs already contain HS/AS (shots), HST/AST (on target), HC/AC (corners), HY/AY/HR/AR (cards)
  per match. Fitting a team-level Poisson model on them is the same method as goals.
- Grading: needs the real numbers after each match, stored in the database. football-data.org's free plan does not return
  match statistics, and the football-data.co.uk CSVs stay dev-only (not stored). So a second source is required.
  API-Football's free plan (about 100 calls a day) has per-match statistics; check its terms and which seasons the
  free plan covers before building. One call per match is affordable for one league.
- Until grading exists, such markets would be unfalsifiable, so they should not be published.

## Hardest: player markets (anytime scorer, player shots on target)
- Needs player-level history (goals, minutes, shots), the squad, and who actually plays. Lineups are announced about an
  hour before kickoff, but the nightly job runs the night before, so "will he play" is unknown at lock time.
- Honest expectation: bookmakers price these with lineup news and large datasets; a results-only model will mostly lose to
  them. If built, ship it labelled experimental, per-player probability = expected scoring rate x probability of starting,
  and let the public record decide.
- Data options: API-Football (players endpoints, free-tier limits), Understat (xG, no official API, check terms).

## Not recommended: in-play markets
They need a live feed, a separate model and a separate record. Do not mix with pre-match results.
