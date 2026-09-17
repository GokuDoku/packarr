"""packarr - season & series packs for anime, mapped right and imported through Sonarr."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, config
from .log import log, setup


def _pipe(args):
    cfg = config.load(args.config)
    setup(cfg.paths.log_file or None)
    from .pipeline import Pipeline
    return cfg, Pipeline(cfg)


def cmd_init(args):
    path = args.path or "packarr.yml"
    if os.path.exists(path) and not args.force:
        raise SystemExit(f"{path} exists (use --force to overwrite)")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(config.EXAMPLE)
    print(f"wrote {path} - fill in the URLs and keys, then `packarr check`")


def cmd_check(args):
    cfg, pipe = _pipe(args)
    ok = True
    try:
        n = len(pipe.sonarr.series())
        print(f"sonarr      ok  ({n} series)")
    except Exception as e:
        print(f"sonarr      FAIL {e}")
        ok = False
    try:
        pipe.tr.torrents(fields=["id"])
        print("transmission ok")
    except Exception as e:
        print(f"transmission FAIL {e}")
        ok = False
    for name, svc in (("radarr", pipe.radarr), ("jellyfin", pipe.jellyfin), ("bazarr", pipe.bazarr)):
        print(f"{name:12s}{'configured' if svc else 'not configured (optional)'}")
    print(f"prowlarr    {'configured' if cfg.prowlarr.enabled else 'not configured (search/auto disabled)'}")
    print(f"downloads   {cfg.paths.downloads_local} ({'exists' if os.path.isdir(cfg.paths.downloads_local) else 'MISSING'}, {pipe.free_gb():.0f} GB free)")
    try:
        pipe.resolver.refresh()
        print("mapping     ok  (anime-lists + fribb tables present)")
    except Exception as e:
        print(f"mapping     FAIL {e}")
        ok = False
    sys.exit(0 if ok else 1)


def cmd_search(args):
    cfg, pipe = _pipe(args)
    if not cfg.prowlarr.enabled:
        raise SystemExit("prowlarr is not configured")
    from .clients.prowlarr import Prowlarr
    from .search import save_last, search
    rows = search(Prowlarr(cfg.prowlarr.url, cfg.prowlarr.api_key, cfg.prowlarr.indexer_ids), args.query, cfg.search,
                  episodes=args.episodes, dual_only=args.dual)
    for i, r in enumerate(rows[:args.limit]):
        print(f"{i:3d}  {r['score']:4d}  {r.get('seeders', 0):4d}s {r['gb']:7.1f} GB  {r['title'][:95]}   [{', '.join(r['why'])}]")
    p = save_last(rows, cfg.paths.state_dir)
    print(f"-- {len(rows)} candidates; `packarr add <row> --series <id>` (rows saved to {p})")


def cmd_add(args):
    cfg, pipe = _pipe(args)
    if args.selection.isdigit():
        from .search import load_last
        row = load_last(cfg.paths.state_dir)[int(args.selection)]
    else:
        row = {"title": args.title or args.selection, "guid": args.selection, "gb": args.gb or 0, "info": args.info or ""}
    opts = {"all": args.all, "gb": args.gb}
    if args.langs:
        opts["languages"] = args.langs.split(",")
    if args.map:
        opts["map"] = [[int(x) for x in m.split(":")] for m in args.map.split(",")]
    if args.abs:
        opts["abs"] = [[int(a), int(b)] for a, b in (x.split("-") for x in args.abs.split(","))]
    if args.dirs:
        opts["dirs"] = args.dirs.split(",")
    pipe.add(row, args.series, **opts)


def cmd_adopt(args):
    cfg, pipe = _pipe(args)
    opts = {"all": args.all}
    if args.langs:
        opts["languages"] = args.langs.split(",")
    pipe.adopt(args.torrent, args.series, **opts)


def cmd_run(args):
    cfg, pipe = _pipe(args)
    if args.interval:
        log(f"packarr {__version__} daemon: tick every {args.interval}s")
        while True:
            try:
                pipe.run()
            except Exception as e:
                log(f"tick failed: {e}")
            time.sleep(args.interval)
    pipe.run()


def cmd_status(args):
    cfg, pipe = _pipe(args)
    s = pipe.load()
    print(f"free {pipe.free_gb():.0f} GB   jobs {len(s['jobs'])}")
    for i, j in enumerate(s["jobs"]):
        if args.all or j["status"] not in ("done", "superseded", "cancelled"):
            print(f"{i:3d} {j['status']:12s} {j['gb']:7.1f} GB {str(j.get('imported', '')):>4} {j['series'][:28]:28s} {j['title'][:70]}")


def cmd_plan(args):
    cfg, pipe = _pipe(args)
    s = pipe.load()
    j = s["jobs"][args.job]
    plan, issues, pf = pipe.plan_folder(j, args.folder or pipe.tr.torrents([j["trId"]], ["name"])[0]["name"])
    for r in plan:
        se = f"S{r['se'][0]:02d}E{r['se'][1]:02d}" if r["se"] else "  --  "
        print(f"{'ok ' if r['ok'] else 'BAD'} {se} {r['rel'][-60:]:60s} {r['reason'][:40]} {r['note'][:40]}")
    print(f"-- {len(issues)} issue(s): {issues}   (plan saved to {pf})")


def cmd_approve(args):
    cfg, pipe = _pipe(args)
    explicit = None
    if args.map:
        explicit = {}
        for m in args.map:
            rel, eid = m.rsplit("=", 1)
            explicit[rel] = int(eid)
    if args.map_file:
        with open(args.map_file, encoding="utf-8") as fh:
            explicit = {**(explicit or {}), **{k: int(v) for k, v in json.load(fh).items()}}
    pipe.approve(args.job, explicit, keep=args.keep)


def cmd_subs(args):
    """Find episodes whose files lack a wanted subtitle language and ask Bazarr to fetch them (no re-download)."""
    cfg, pipe = _pipe(args)
    if not cfg.languages.subtitles:
        raise SystemExit("set languages.subtitles (e.g. [eng, jpn]) first")
    if not pipe.jellyfin and not cfg.paths.library_maps and not os.path.isdir(next(iter([s["path"] for s in pipe.sonarr.series()[:1]]), "/nonexistent")):
        raise SystemExit("subtitle checks need the library reachable (mount it; paths.library_maps) or jellyfin configured")
    series = [pipe.sonarr.series_one(args.series)] if args.series else [s for s in pipe.sonarr.series() if s.get("seriesType") == "anime"]
    total = 0
    for s in series:
        missing = pipe.missing_subtitles(s["id"], s["tvdbId"])
        if not missing:
            continue
        print(f"{s['title'][:50]}: {len(missing)} missing subtitle track(s)" + ("" if args.fetch else " (dry run; add --fetch)"))
        for e, lang in missing[:8]:
            print(f"   S{e['seasonNumber']:02d}E{e['episodeNumber']:02d} {lang}")
        if args.fetch:
            if not pipe.bazarr:
                raise SystemExit("bazarr is not configured")
            total += pipe.fill_subtitles(s["id"], s["tvdbId"], args.limit)
    print(f"-- {'requested ' + str(total) + ' from Bazarr' if args.fetch else 'dry run complete'}")


def cmd_serve(args):
    cfg = config.load(args.config)
    setup(cfg.paths.log_file or None)
    from .auto import serve
    serve(cfg)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="packarr", description=__doc__)
    ap.add_argument("--config", "-c", help="path to packarr.yml")
    ap.add_argument("--version", action="version", version=f"packarr {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write an example packarr.yml")
    p.add_argument("path", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("check", help="test every configured connection and the mapping data")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("search", help="search Nyaa (via Prowlarr) for packs and rank them")
    p.add_argument("query")
    p.add_argument("--episodes", type=int, help="episode count, for the MB/episode sanity check")
    p.add_argument("--dual", action="store_true", help="only titles that advertise dual audio")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("add", help="queue a pack: a row number from the last search, or a magnet/URL")
    p.add_argument("selection")
    p.add_argument("--series", type=int, required=True, help="Sonarr series id")
    p.add_argument("--all", action="store_true", help="replace every mapped episode, not just the gaps")
    p.add_argument("--langs", help="languages to stamp on imported files, e.g. japanese (default: config languages.tag)")
    p.add_argument("--map", help="remap seasons: src:dst:offset[,...]  e.g. 21:18:43 for US-numbered dub packs")
    p.add_argument("--abs", help="only pull these absolute episodes: 542-574,783-891")
    p.add_argument("--dirs", help="only pull files whose path contains one of these: S03P01,S03P02")
    p.add_argument("--gb", type=float, help="expected size after selection (for the disk budget)")
    p.add_argument("--title", help="title when adding by magnet/URL")
    p.add_argument("--info", help="Nyaa view URL when adding by magnet (lets Packarr fetch the .torrent)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("adopt", help="take over a torrent that was added to the client by hand")
    p.add_argument("torrent", type=int, help="torrent id in the client")
    p.add_argument("--series", type=int, required=True)
    p.add_argument("--all", action="store_true")
    p.add_argument("--langs")
    p.set_defaults(fn=cmd_adopt)

    p = sub.add_parser("run", help="one tick of the pipeline (cron), or a daemon with --interval")
    p.add_argument("--interval", type=int, help="seconds between ticks; omit for a single tick")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("status", help="list jobs")
    p.add_argument("--all", action="store_true", help="include finished jobs")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("plan", help="(re)plan a job's folder and print the proposal without importing")
    p.add_argument("job", type=int)
    p.add_argument("--folder", help="folder name under the downloads dir (default: the job's torrent)")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("approve", help="release a held job, optionally with hand mappings")
    p.add_argument("job", type=int)
    p.add_argument("--map", action="append", help="'relative/path.mkv=<sonarr episode id>' (repeatable)")
    p.add_argument("--map-file", help="JSON {relative path: episode id}")
    p.add_argument("--keep", action="store_true", help="keep the torrent after import instead of routing leftovers")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("subs", help="episodes whose files lack a wanted subtitle language; --fetch asks Bazarr for them")
    p.add_argument("--series", type=int, help="one Sonarr series id (default: every anime series)")
    p.add_argument("--fetch", action="store_true", help="actually request the subtitles from Bazarr")
    p.add_argument("--limit", type=int, default=0, help="at most this many requests per series (0 = all)")
    p.set_defaults(fn=cmd_subs)

    p = sub.add_parser("serve", help="listen for Sonarr 'On Series Add' webhooks (auto mode)")
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
