"""Planner behaviour on the pack shapes that caused trouble in the wild. Each test is a real incident."""

from __future__ import annotations

from conftest import FakeResolver, entry, episodes, items, job, mapping


def test_sxxeyy_filenames_map_directly(make_planner):
    pl = make_planner()
    eps = episodes({1: 12, 2: 12})
    plan, issues = pl.plan(job("Redo of Healer", 388575), items([f"[Judas] Kaifuku - S01E{n:02d}.mkv" for n in range(1, 13)]), eps, {})
    assert not issues
    assert mapping(plan)["[Judas] Kaifuku - S01E07.mkv"] == (1, 7)


def test_season_x_episode_style(make_planner):
    """Rurouni Kenshin (DragonFox): '2x11' filenames were 95 unmapped files until SEX was added."""
    pl = make_planner()
    eps = episodes({1: 27, 2: 35, 3: 33})
    plan, issues = pl.plan(job("Rurouni Kenshin", 70863), items(["Rurouni Kenshin - 2x11 - The Creator.mkv", "Rurouni Kenshin - 1x20 - Revival.mkv"]), eps, {})
    assert not issues
    m = mapping(plan)
    assert m["Rurouni Kenshin - 2x11 - The Creator.mkv"] == (2, 11)
    assert m["Rurouni Kenshin - 1x20 - Revival.mkv"] == (1, 20)


def test_underscore_season_token_is_not_season_one(make_planner):
    """Attack on Titan S3 ([DB]): 'S3_-_07' + no resolver hit used to fall through to the S1 default.
    Twelve files landed on S01, were 'already dual', were skipped, and the pack was deleted."""
    pl = make_planner()
    eps = episodes({1: 25, 2: 12, 3: 22, 4: 30})
    files = [f"[DB]Attack on Titan S3_-_{n:02d}_(Dual Audio_10bit_BD1080p_x265).mkv" for n in range(1, 13)]
    plan, issues = pl.plan(job("Attack on Titan", 267440, "[DB] Shingeki no Kyojin Season 3 | Attack on Titan Season 3 [Dual Audio]"), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[6]] == (3, 7)


def test_refuses_s1_default_when_pack_title_names_another_season(make_planner):
    pl = make_planner()
    eps = episodes({1: 25, 2: 12, 3: 22})
    files = [f"Show - {n:02d}.mkv" for n in range(1, 6)]  # no season token in the files at all
    plan, issues = pl.plan(job("Show", 1, "[Grp] Show Season 3 [BD 1080p]"), items(files), eps, {})
    # root files borrow the pack title's season: S3, not S1
    assert mapping(plan)["Show - 03.mkv"] == (3, 3)
    assert not issues


def test_resolver_is_authoritative_over_cour_numbering(make_planner):
    """Bungou Stray Dogs 'Season 03' on AniList is TVDB S2 (split cour). anime-lists says so; the filename does not."""
    res = FakeResolver({"Bungou Stray Dogs Season 3": entry(305, 2, 0, 12, "Bungou Stray Dogs 3rd Season")})
    pl = make_planner(res)
    eps = episodes({1: 24, 2: 12, 3: 12})
    files = [f"Bungou Stray Dogs Season 03/[Anime Time] Bungou Stray Dogs Season 03 - {n:02d}.mkv" for n in range(1, 13)]
    plan, issues = pl.plan(job("Bungo Stray Dogs", 305), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[2]] == (2, 3)
    assert all(r["reason"].startswith("anime-lists") for r in plan)


def test_continuous_multi_folder_pack_is_absolute(make_planner):
    """Judas-style: Season 1/01-25, Season 2/26-37 - the folders form one run, so numbers are absolute."""
    pl = make_planner()
    eps = episodes({1: 25, 2: 12})
    files = [f"Season 1/Show - {n:02d}.mkv" for n in range(1, 26)] + [f"Season 2/Show - {n:02d}.mkv" for n in range(26, 38)]
    plan, issues = pl.plan(job("Show", 1), items(files), eps, {})
    assert not issues
    assert mapping(plan)["Season 2/Show - 30.mkv"] == (2, 5)


def test_extras_and_ovas_are_not_episodes(make_planner):
    pl = make_planner()
    eps = episodes({1: 12})
    files = ["Show - 01.mkv", "NC/OP.mkv", "Extras/NCED.mkv", "Show - OVA [1080p].mkv", "Show - 06.5 (Recap).mkv"]
    plan, issues = pl.plan(job("Show", 1), items(files), eps, {})
    assert not issues
    rows = {r["rel"]: r["reason"] for r in plan}
    assert rows["NC/OP.mkv"] == "extra" and rows["Extras/NCED.mkv"] == "extra"
    assert rows["Show - OVA [1080p].mkv"].startswith("movie/OVA")
    assert rows["Show - 06.5 (Recap).mkv"].startswith("movie/OVA")
    assert mapping(plan)["Show - 01.mkv"] == (1, 1)


def test_explicit_map_beats_the_extras_filter(make_planner):
    """Monster: TVDB has 'Opening Creditless' specials, so NC/OP.mkv can be pinned to S00E03 by hand."""
    pl = make_planner()
    eps = episodes({1: 3}, specials=["Extra 1 - The Beginning (Recap)", "The Nameless Monster", "Monster Opening Creditless"])
    op_id = next(e["id"] for e in eps if e["title"] == "Monster Opening Creditless")
    plan, issues = pl.plan(job("Monster", 69, explicit={"NC/OP.mkv": op_id}), items(["NC/OP.mkv", "NC/ED.mkv"]), eps, {})
    assert not issues
    m = mapping(plan)
    assert m["NC/OP.mkv"] == (0, 3) and m["NC/ED.mkv"] is None


def test_unmatched_numbered_extra_holds_the_plan(make_planner):
    """Monster's 'Extra 1 - Origin' has no number and no confident title match: HELD, not guessed."""
    pl = make_planner(durations={"[Anime Time] Monster Extra 1 - Origin.mkv": 24.0})
    eps = episodes({1: 74}, specials=["Extra 1 - The Beginning (Recap)", "The Nameless Monster"])
    files = ["[Anime Time] Monster - 01.mkv", "[Anime Time] Monster Extra 1 - Origin.mkv"]
    plan, issues = pl.plan(job("Monster", 69, "[Anime Time] Monster Complete [Dual Audio]"), items(files), eps, {})
    assert issues and "unmapped" in issues[0]


def test_season_remap_for_us_numbered_dub_packs(make_planner):
    """Pokémon: the dub's 'Season 21' is TVDB S18 starting at E44 -> --map 21:18:43."""
    pl = make_planner()
    eps = episodes({18: 100, 19: 50})
    files = [f"Pokemon Season 21/Pokemon - S21E{n:02d}.mkv" for n in range(1, 4)]
    plan, issues = pl.plan(job("Pokémon", 76703, "Pokemon Sun & Moon Season 21 [DUB]", map=[[21, 18, 43]]), items(files), eps, {})
    assert not issues
    assert mapping(plan)["Pokemon Season 21/Pokemon - S21E02.mkv"] == (18, 45)
    assert all(r["reason"].startswith("--map") for r in plan)


def test_duplicate_targets_are_an_issue(make_planner):
    pl = make_planner()
    eps = episodes({1: 12})
    plan, issues = pl.plan(job("Show", 1), items(["Show - 01.mkv", "Show - S01E01.mkv"]), eps, {})
    assert any("2 files -> S01E01" in i for i in issues)


def test_duplicate_copies_keep_the_largest_quietly(make_planner):
    pl = make_planner()
    eps = episodes({1: 12})
    its = items(["Show - 01.mkv", "Show - 01 (1).mkv"])
    its[0]["size"] = 100
    plan, issues = pl.plan(job("Show", 1), its, eps, {})
    assert not issues
    by = {r["rel"]: r for r in plan}
    assert by["Show - 01 (1).mkv"]["se"] == (1, 1) and by["Show - 01.mkv"]["note"] == "duplicate copy"


def test_wrong_duration_is_an_issue_not_an_import(make_planner):
    """A 97 MB, 10-minute file wearing an episode number must never replace a real episode."""
    pl = make_planner(durations={"Show - 05.mkv": 9.0})
    eps = episodes({1: 12}, runtime=24)
    plan, issues = pl.plan(job("Show", 1), items(["Show - 05.mkv"]), eps, {})
    assert issues and "duration" in issues[0]
    assert plan[0]["ok"] is False


def test_long_file_with_episode_number_is_routed_as_a_movie(make_planner):
    pl = make_planner(durations={"Show - 01.mkv": 95.0})
    eps = episodes({1: 12}, runtime=24)
    plan, issues = pl.plan(job("Show", 1), items(["Show - 01.mkv"]), eps, {})
    assert not issues
    assert plan[0]["reason"].startswith("movie/OVA") and plan[0]["epId"] is None


def test_stale_anime_lists_table_falls_back_to_positional(make_planner):
    """InuYasha: the table said S6 has 21 episodes, TVDB has 26 -> distrust it, map positionally through the seasons."""
    maps = [{"anidbseason": 1, "tvdbseason": 6, "start": 1, "end": 21, "offset": 0, "pairs": {}}]
    res = FakeResolver({"InuYasha": entry(77086, "a", 0, 167, "InuYasha", maps)})
    pl = make_planner(res)
    eps = episodes({1: 27, 2: 27, 3: 27, 4: 27, 5: 26, 6: 33})
    files = ["InuYasha/InuYasha - 030.mkv"]
    plan, issues = pl.plan(job("InuYasha", 77086), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[0]] == (2, 3)


def test_foreign_folder_goes_to_the_other_sonarr_series(make_planner):
    """A Sailor Moon pack with a 'Crystal' subfolder: those files belong to a different TVDB series."""
    res = FakeResolver({"Crystal": entry(281683, 1, 0, 13, "Sailor Moon Crystal"), "Sailor Moon": entry(78500, 1, 0, 46, "Sailor Moon")})
    other = episodes({1: 13})
    pl = make_planner(res, others={7: other})
    eps = episodes({1: 46})
    files = ["2-) Crystal/Sailor Moon Crystal - 03.mkv", "Sailor Moon - 03.mkv"]
    plan, issues = pl.plan(job("Sailor Moon", 78500), items(files), eps, {"281683": {"id": 7, "title": "Sailor Moon Crystal"}})
    assert not issues
    by = {r["rel"]: r for r in plan}
    assert by[files[0]]["seriesId"] == 7 and by[files[0]]["se"] == (1, 3)
    assert by[files[1]]["seriesId"] == 1 and by[files[1]]["se"] == (1, 3)


def test_xem_second_opinion_overrides_positional_fallback_when_stale(make_planner):
    """Same stale InuYasha table as above, but XEM has its own opinion on episode 30 - and it wins."""
    maps = [{"anidbseason": 1, "tvdbseason": 6, "start": 1, "end": 21, "offset": 0, "pairs": {}}]
    res = FakeResolver({"InuYasha": entry(77086, "a", 0, 167, "InuYasha", maps)}, xem={(77086, 30): (2, 4)})
    pl = make_planner(res)
    eps = episodes({1: 27, 2: 27, 3: 27, 4: 27, 5: 26, 6: 33})
    files = ["InuYasha/InuYasha - 030.mkv"]
    plan, issues = pl.plan(job("InuYasha", 77086), items(files), eps, {})
    assert not issues
    row = next(r for r in plan if r["rel"] == files[0])
    assert row["se"] == (2, 4)  # XEM's answer, not positional (2, 3)
    assert "xem" in row["note"].lower()


def test_xem_falls_through_to_heuristics_when_it_has_no_data_for_this_episode(make_planner):
    """XEM has data for this show, but not for the specific episode in question - the old fallback still runs."""
    maps = [{"anidbseason": 1, "tvdbseason": 6, "start": 1, "end": 21, "offset": 0, "pairs": {}}]
    res = FakeResolver({"InuYasha": entry(77086, "a", 0, 167, "InuYasha", maps)}, xem={(77086, 99): (5, 1)})
    pl = make_planner(res)
    eps = episodes({1: 27, 2: 27, 3: 27, 4: 27, 5: 26, 6: 33})
    files = ["InuYasha/InuYasha - 030.mkv"]
    plan, issues = pl.plan(job("InuYasha", 77086), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[0]] == (2, 3)  # unchanged: positional fallback, as before XEM existed


def test_xem_answer_ignored_if_it_names_an_episode_that_does_not_exist(make_planner):
    """XEM points at a season/episode Sonarr doesn't have - don't trust a nonexistent target, fall through."""
    maps = [{"anidbseason": 1, "tvdbseason": 6, "start": 1, "end": 21, "offset": 0, "pairs": {}}]
    res = FakeResolver({"InuYasha": entry(77086, "a", 0, 167, "InuYasha", maps)}, xem={(77086, 30): (9, 99)})
    pl = make_planner(res)
    eps = episodes({1: 27, 2: 27, 3: 27, 4: 27, 5: 26, 6: 33})
    files = ["InuYasha/InuYasha - 030.mkv"]
    plan, issues = pl.plan(job("InuYasha", 77086), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[0]] == (2, 3)  # falls back to positional, XEM's bogus target rejected


def test_xem_not_consulted_when_the_table_is_not_stale(make_planner):
    """No disagreement, no XEM lookup at all - only a stale table triggers the second opinion."""
    class TrackingResolver(FakeResolver):
        def xem_tvdb(self, tvdb_id, anidb_episode):
            self.xem_calls = getattr(self, "xem_calls", 0) + 1
            return super().xem_tvdb(tvdb_id, anidb_episode)

    res = TrackingResolver({"Cowboy Bebop": entry(76885, 1, 0, 26, "Cowboy Bebop")})
    pl = make_planner(res)
    eps = episodes({1: 26})
    files = ["Cowboy Bebop/Cowboy Bebop - 03.mkv"]
    plan, issues = pl.plan(job("Cowboy Bebop", 76885), items(files), eps, {})
    assert not issues
    assert mapping(plan)[files[0]] == (1, 3)
    assert getattr(res, "xem_calls", 0) == 0


def test_stale_table_with_numbered_default_season_stays_in_that_season(make_planner):
    """Hetalia: anime-lists still says episodes 27-52 are TVDB S2, but TVDB merged them into a 52-episode S1."""
    maps = [{"anidbseason": 1, "tvdbseason": 1, "start": 1, "end": 26, "offset": 0, "pairs": {}},
            {"anidbseason": 1, "tvdbseason": 2, "start": 27, "end": 52, "offset": -26, "pairs": {}}]
    res = FakeResolver({"Hetalia Axis Powers": entry(88161, 1, 0, 52, "Hetalia Axis Powers", maps)})
    pl = make_planner(res)
    eps = episodes({1: 52, 2: 48})
    files = ["Hetalia Axis Powers/Hetalia_Axis_Powers_Ep27_(CC6866E3).mkv", "Hetalia Axis Powers/Hetalia_Axis_Powers_Ep05_(AAAA0000).mkv"]
    plan, issues = pl.plan(job("Hetalia - Axis Powers", 88161), items(files), eps, {})
    assert not issues
    m = mapping(plan)
    assert m[files[0]] == (1, 27) and m[files[1]] == (1, 5)


def test_series_without_episodes_is_held_not_binned(make_planner):
    """A freshly added series has no episode list for a minute; the pack must be held, not treated as 13 extras and deleted."""
    pl = make_planner()
    plan, issues = pl.plan(job("Arifureta", 357019), items([f"[EMBER] Arifureta - S01E{n:02d}.mkv" for n in range(1, 14)]), [], {})
    assert issues and "no episodes" in issues[0]


def test_stale_table_prefers_the_season_whose_size_matches(make_planner):
    """Hetalia World Series: the table says TVDB S3, but TVDB renumbered - the 48-episode season is S2."""
    maps = [{"anidbseason": 1, "tvdbseason": 3, "start": 1, "end": 24, "offset": 0, "pairs": {}},
            {"anidbseason": 1, "tvdbseason": 4, "start": 25, "end": 48, "offset": -24, "pairs": {}}]
    res = FakeResolver({"Hetalia World Series": entry(88161, 3, 0, 48, "Hetalia World Series", maps)})
    pl = make_planner(res)
    eps = episodes({1: 52, 2: 48, 3: 25, 4: 15})
    files = ["Hetalia World Series/Hetalia_World_Series_Ep07_(AAAA0000).mkv", "Hetalia World Series/Hetalia_World_Series_Ep40_(BBBB0000).mkv"]
    plan, issues = pl.plan(job("Hetalia - Axis Powers", 88161), items(files), eps, {})
    assert not issues
    m = mapping(plan)
    assert m[files[0]] == (2, 7) and m[files[1]] == (2, 40)
