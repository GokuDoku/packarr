# Contributing

Packarr is small on purpose: one dependency, plain `urllib`, and a planner you can read top to bottom.

## Dev loop

```bash
pip install -e ".[dev]"
ruff check packarr tests
pytest -q
```

## The one rule for planner changes

Every heuristic in `planner.py` / `parsing.py` exists because a real pack broke the previous rule. When you change one:

1. add the pack shape that motivated it to `tests/test_planner.py` (file names + expected `(season, episode)`),
2. keep the docstring's "why" — the next person needs it more than the regex.

Fixture helpers in `tests/conftest.py` build Sonarr-shaped episode lists and a scripted resolver, so tests need no network, no ffprobe and no Sonarr.

## Mapping data

Packarr does not maintain its own title→TVDB tables. Wrong season/offset for a show is almost always an
[Anime-Lists](https://github.com/Anime-Lists/anime-lists) or [Fribb/anime-lists](https://github.com/Fribb/anime-lists)
issue — fix it upstream and everyone (Kometa, Shoko, HAMA, Packarr) benefits.
