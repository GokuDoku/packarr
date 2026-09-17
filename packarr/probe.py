"""ffprobe wrappers. Release names lie; the file is the only witness."""

from __future__ import annotations

import json
import os
import subprocess

from . import parsing as P

_EXTRA = P.EXTRA_FILE


def duration_min(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip()) / 60
    except ValueError:
        return 0.0


def height(path: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return int(out.stdout.strip().split(",")[0])
    except ValueError:
        return 0


def streams(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,height:stream_tags=language,title", "-of", "json", path],
                         capture_output=True, text=True)
    try:
        st = json.loads(out.stdout)["streams"]
    except Exception:
        return {"error": f"ffprobe failed on {os.path.basename(path)}"}
    audio = [(x.get("tags") or {}).get("language", "und") + ((":" + (x.get("tags") or {}).get("title", "")) if (x.get("tags") or {}).get("title") else "") for x in st if x["codec_type"] == "audio"]
    subs = [(x.get("tags") or {}).get("language", "und") for x in st if x["codec_type"] == "subtitle"]
    vid = [x for x in st if x["codec_type"] == "video" and x["codec_name"] not in ("mjpeg", "png", "bmp", "gif", "webp")]  # cover art is not a video track
    return {"height": vid[0]["height"] if vid else 0, "codec": vid[0]["codec_name"] if vid else "?", "audio": audio, "subs": subs}


_LANG_ALIASES = {"eng": {"eng", "en", "english"}, "jpn": {"jpn", "ja", "japanese"}}


def has_language(tags: list[str], lang: str) -> bool:
    """Do any of these stream language tags mean `lang`? Groups write 'eng', 'en' or 'English'."""
    want = _LANG_ALIASES.get(lang, {lang})
    return any(t.split(":")[0].lower() in want for t in tags)


def sample_pack(root: str, wanted: str = "eng", subtitles: list[str] | None = None) -> tuple[bool, str, list[str]]:
    """Probe first/middle/last episode file: do they carry the wanted audio, and the wanted subtitle languages?
    Returns (audio_ok, summary, missing_subtitle_langs). Informational unless `languages.subtitles_required`."""
    vids = []
    for dp, _, fs in os.walk(root):
        vids += [os.path.join(dp, f) for f in fs if P.is_video(f)]
    eps = [v for v in vids if not _EXTRA.search(os.path.relpath(v, root)) and not P.EXTRA_DIR.search(os.path.relpath(v, root))]
    vids = sorted(eps or vids)
    if not vids:
        return False, "no video files"
    sample = [vids[0], vids[len(vids) // 2], vids[-1]] if len(vids) > 2 else vids
    summ, bad, missing = [], 0, set()
    for v in dict.fromkeys(sample):
        s = streams(v)
        if "error" in s:
            return False, s["error"], []
        summ.append(f"{os.path.basename(v)[:40]}: {s['codec']} {s['height']}p audio={s['audio']} subs={s['subs']}")
        if not has_language(s["audio"], wanted):
            bad += 1
        for lang in subtitles or []:
            if not has_language(s["subs"], lang):
                missing.add(lang)
    return bad <= len(summ) // 2, "; ".join(summ), sorted(missing)
