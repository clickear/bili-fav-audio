import json
import os
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests

UID = os.environ["BILI_UID"]
FID = os.environ["BILI_FID"]
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TAG = os.environ.get("AUDIO_TAG", "audio")
COOKIE = os.environ.get("BILI_COOKIE", "").strip()
MEDIA_TYPE = os.environ.get("MEDIA_TYPE", "audio").strip().lower()
VIDEO_FORMAT = os.environ.get("VIDEO_FORMAT", "").strip() or "bv*[vcodec^=avc][height<=1080]+ba/bv*[height<=1080]+ba/b"
SITE_BASE = os.environ.get("SITE_BASE", f"https://github.com/{REPO}/releases/download/{TAG}")

STATE_FILE = Path("state.json")
COOKIE_FILE = Path("cookies.txt")
API = "https://api.bilibili.com/x/v3/fav/resource/list"

KINDS = {
    "audio": {"dir": Path("audio"), "ext": "m4a", "feed": Path("feed_audio.xml"), "mime": "audio/mp4", "label": "音频"},
    "video": {"dir": Path("video"), "ext": "mp4", "feed": Path("feed_video.xml"), "mime": "video/mp4", "label": "视频"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
if COOKIE:
    HEADERS["Cookie"] = COOKIE


def enabled_kinds():
    if MEDIA_TYPE == "both":
        return ["audio", "video"]
    if MEDIA_TYPE in KINDS:
        return [MEDIA_TYPE]
    return ["audio"]


def write_cookies():
    if not COOKIE:
        return
    lines = ["# Netscape HTTP Cookie File"]
    for part in COOKIE.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", name, value]))
    COOKIE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {"info": {}, "items": {}}
    for it in state.get("items", {}).values():
        if "size" in it and "audio" not in it:
            it["audio"] = {"size": it.pop("size"), "file": f"{it['bvid']}.m4a"}
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_list():
    items, info, pn = [], {}, 1
    while True:
        params = {"media_id": FID, "pn": pn, "ps": 20, "order": "mtime", "type": 0, "tid": 0, "jsonp": "jsonp"}
        data = requests.get(API, params=params, headers=HEADERS, timeout=30).json().get("data") or {}
        if not info:
            info = data.get("info") or {}
        items.extend(data.get("medias") or [])
        if not data.get("has_more"):
            break
        pn += 1
    return items, info


def download(kind, bvid):
    cfg = KINDS[kind]
    out = cfg["dir"] / f"{bvid}.{cfg['ext']}"
    if out.exists():
        return out
    cmd = [
        "yt-dlp", "--no-update", "--no-playlist", "--referer", "https://www.bilibili.com/",
        "-o", str(cfg["dir"] / f"{bvid}.%(ext)s"),
    ]
    if kind == "audio":
        cmd += ["-f", "ba", "-x", "--audio-format", "m4a"]
    else:
        cmd += ["-f", VIDEO_FORMAT, "--merge-output-format", "mp4"]
    if COOKIE_FILE.exists():
        cmd += ["--cookies", str(COOKIE_FILE)]
    cmd.append(f"https://www.bilibili.com/video/{bvid}")
    subprocess.run(cmd, check=True)
    return out


def ensure_release():
    if not REPO:
        return
    subprocess.run(
        ["gh", "release", "create", TAG, "--title", "media", "--notes", "media assets", "--latest=false"],
        capture_output=True,
    )


def upload_asset(path):
    if not REPO:
        return
    subprocess.run(["gh", "release", "upload", TAG, str(path), "--clobber"], check=True)


def rfc822(ts):
    return format_datetime(datetime.fromtimestamp(ts, tz=timezone.utc))


def build_feed(kind, state):
    cfg = KINDS[kind]
    info = state["info"]
    title = f"{info.get('title', 'bilibili 收藏夹')} - {cfg['label']}"
    cover = info.get("cover", "")
    home = f"https://space.bilibili.com/{UID}/favlist?fid={FID}"
    now = format_datetime(datetime.now(tz=timezone.utc))

    items = [v for v in state["items"].values() if v.get(kind)]
    ordered = sorted(items, key=lambda x: x["ctime"], reverse=True)
    entries = []
    for it in ordered:
        url = f"{SITE_BASE}/{it['bvid']}.{cfg['ext']}"
        size = it[kind].get("size", 0)
        entries.append(f"""    <item>
      <title>{escape(it['title'])}</title>
      <link>https://www.bilibili.com/video/{it['bvid']}</link>
      <guid isPermaLink="false">{it['bvid']}-{kind}</guid>
      <pubDate>{rfc822(it['ctime'])}</pubDate>
      <author>{escape(it.get('author', ''))}</author>
      <description>{escape(it.get('intro', ''))}</description>
      <itunes:image href="{escape(it.get('cover', ''))}"/>
      <enclosure url="{escape(url)}" type="{cfg['mime']}" length="{size}"/>
    </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(title)}</title>
    <link>{home}</link>
    <description>{escape(title)}</description>
    <language>zh-cn</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:image href="{escape(cover)}"/>
    <itunes:author>bilibili</itunes:author>
{chr(10).join(entries)}
  </channel>
</rss>
"""
    cfg["feed"].write_text(xml, encoding="utf-8")


def main():
    kinds = enabled_kinds()
    for k in kinds:
        KINDS[k]["dir"].mkdir(exist_ok=True)
    write_cookies()
    state = load_state()
    medias, info = fetch_list()
    if info:
        state["info"] = info
    ensure_release()

    for m in medias:
        if m.get("attr", 0) != 0:
            continue
        bvid = m["bvid"]
        it = state["items"].setdefault(bvid, {})
        it.update({
            "bvid": bvid,
            "title": m.get("title", ""),
            "intro": m.get("intro", ""),
            "cover": m.get("cover", ""),
            "author": (m.get("upper") or {}).get("name", ""),
            "ctime": m.get("ctime", 0),
        })
        for kind in kinds:
            if it.get(kind):
                continue
            try:
                path = download(kind, bvid)
            except subprocess.CalledProcessError:
                continue
            upload_asset(path)
            it[kind] = {"size": path.stat().st_size if path.exists() else 0, "file": path.name}
            save_state(state)

    for kind in kinds:
        build_feed(kind, state)
    save_state(state)


if __name__ == "__main__":
    main()
