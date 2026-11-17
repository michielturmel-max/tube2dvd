#!/usr/bin/env python3

import argparse
import hashlib
import importlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import textwrap
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_CATEGORIES = [
    "Muziek",
    "Documentaire",
    "Film",
    "Serie",
    "Tech",
    "Humor",
    "Kinderen",
    "Overig",
]

VERSION = "1.1.0"
UPDATE_REPO = os.environ.get("TUBE2DVD_REPO", "michielturmel-max/tube2dvd")

DISC_BYTES = 4_700_000_000
DEFAULT_BUDGET = 4_600_000_000
DEFAULT_TARGET = 4_000_000_000
NEWS_CATEGORY = "Nieuws van vandaag"
TEXT_CATEGORY = "Teksten"

NEWS_PLUGIN_CANDIDATES = [
    Path.home() / ".config/omarchy/plugins/tweakers.nieuws/bin/tweakers-nieuws",
    Path("/usr/local/bin/tweakers-nieuws"),
]

JSON_MODE = False
STYLE = "scrapbook"

MENU_ROWS = 7
MENU_ITEM_Y = 132
MENU_ROW_H = 46
MENU_FOOTER_Y = 506

TV = {
    "pal": {"w": 720, "h": 576, "canvas": (1024, 576), "sar": "64/45", "fps": 25},
    "ntsc": {"w": 720, "h": 480, "canvas": (854, 480), "sar": "32/27", "fps": 30000 / 1001},
}

FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


class Fail(Exception):
    pass


def log(msg=""):
    if not JSON_MODE:
        print(msg, flush=True)


def progress(label, value, total, width=28, bytes_mode=True):
    total = max(1, total)
    pct = min(100, max(0, value / total * 100))
    n = int(width * pct / 100)
    bar = "#" * n + "." * (width - n)
    sizes = f"{human(value)}/{human(total)}" if bytes_mode else f"{int(value)}/{int(total)}"
    log(f"{label:<12} [{bar}] {pct:3.0f}%  {sizes}")


def emit(data):
    if JSON_MODE:
        print(json.dumps(data, ensure_ascii=False), flush=True)


def die(msg):
    raise Fail(msg)


def parse_size(text):
    if isinstance(text, (int, float)):
        return int(text)
    value = str(text).strip().lower().replace("_", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?b?)", value)
    if not match:
        die(f"ongeldige grootte: {text}")
    n = float(match.group(1))
    suffix = match.group(2)
    power = {"": 0, "k": 1, "kb": 1, "m": 2, "mb": 2, "g": 3, "gb": 3, "t": 4, "tb": 4}[suffix]
    return int(n * 1000**power)


def wrap_lines(text, width=52, limit=3):
    parts = textwrap.wrap(str(text or ""), width=width, max_lines=limit, placeholder="…")
    return parts or [""]


def wrap_block(text, width, limit):
    return [escape(x) for x in wrap_lines(text, width, limit)]


def which(name):
    return shutil.which(name)


def require(*tools):
    missing = [t for t in tools if not which(t)]
    if missing:
        pkgs = sorted({pkg_for(t) for t in missing})
        die(
            "ontbrekende programma's: "
            + ", ".join(missing)
            + "\n  installeer met: sudo pacman -S "
            + " ".join(pkgs)
        )


def notify(title, message):
    if which("notify-send"):
        subprocess.run(["notify-send", title, message], check=False)
    log(f"MELDING: {title} — {message}")


def pkg_for(tool):
    return {
        "yt-dlp": "yt-dlp",
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffmpeg",
        "mkisofs": "cdrtools",
        "genisoimage": "cdrtools",
        "xorriso": "libisoburn",
        "growisofs": "dvd+rw-tools",
        "dvdauthor": "dvdauthor",
        "spumux": "dvdauthor",
        "fc-match": "fontconfig",
    }.get(tool, tool)


def run(cmd, capture=False, check=True):
    if capture:
        r = subprocess.run(cmd, text=True, capture_output=True)
    else:
        r = subprocess.run(cmd, text=True)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip() if capture else ""
        die(f"commando mislukt ({r.returncode}): {' '.join(cmd)}\n{err}")
    return r


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def human_dur(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def slug(text, limit=80):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9 _.-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text[:limit].strip() or "video")


def safe_name(text):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text).strip()
    return text[:60] or "Overig"


def font_path():
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    if which("fc-match"):
        r = run(["fc-match", "-f", "%{file}", "DejaVu Sans"], capture=True, check=False)
        if r.returncode == 0 and Path(r.stdout.strip()).is_file():
            return r.stdout.strip()
    die("geen bruikbaar font gevonden (installeer ttf-dejavu)")


def device_path():
    for dev in ("/dev/sr0", "/dev/dvd", "/dev/cdrom"):
        if Path(dev).exists():
            return dev
    return "/dev/sr0"


class Library:
    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.db_path = self.root / "library.json"
        self.cat_path = self.root / "categories.json"
        self.disc_path = self.root / "discs.json"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "videos").mkdir(exist_ok=True)
        (self.root / "thumbs").mkdir(exist_ok=True)
        if not self.cat_path.exists():
            self.cat_path.write_text(json.dumps(DEFAULT_CATEGORIES, indent=2, ensure_ascii=False) + "\n")
        self.db = self.read(self.db_path, {"version": 1, "items": []})
        self.discs = self.read(self.disc_path, [])
        self.backfill_video_posters()

    def backfill_video_posters(self):
        changed = False
        for item in self.items:
            if item.get("kind") != "news" or not item.get("video"):
                continue
            poster = item.get("video_poster") or ""
            if poster and (self.root / poster).is_file():
                continue
            fresh = ensure_video_poster(self.root, item["id"])
            if fresh:
                item["video_poster"] = fresh
                changed = True
        if changed:
            self.save()

    @staticmethod
    def read(path, default):
        if not Path(path).exists():
            return default
        try:
            return json.loads(Path(path).read_text())
        except json.JSONDecodeError:
            die(f"kapot json-bestand: {path}")

    def save(self):
        self.db_path.write_text(json.dumps(self.db, indent=1, ensure_ascii=False) + "\n")

    def save_discs(self):
        self.disc_path.write_text(json.dumps(self.discs, indent=1, ensure_ascii=False) + "\n")

    @property
    def items(self):
        return self.db["items"]

    def categories(self):
        cats = list(self.read(self.cat_path, DEFAULT_CATEGORIES))
        for item in self.items:
            if item["category"] not in cats:
                cats.append(item["category"])
        return cats

    def add_category(self, name):
        cats = self.read(self.cat_path, DEFAULT_CATEGORIES)
        if name not in cats:
            cats.append(name)
            self.cat_path.write_text(json.dumps(cats, indent=2, ensure_ascii=False) + "\n")

    def find(self, needle):
        needle = needle.lower()
        hits = [
            i
            for i in self.items
            if needle in i["id"].lower() or needle in i["title"].lower()
        ]
        if not hits:
            die(f"niets gevonden voor '{needle}'")
        if len(hits) > 1:
            for i in hits:
                log(f"  {i['id']}  {i['title']}")
            die("meerdere treffers, gebruik de video-id")
        return hits[0]

    def size(self):
        return sum(
            (self.root / f["file"]).stat().st_size
            for f in self.items
            if f.get("kind") != "news" and f.get("file") and (self.root / f["file"]).exists()
        )

    def index_html(self, items=None):
        write_html(self, self.items if items is None else items, self.root / "index.html")


def ask(prompt, default=""):
    try:
        val = input(prompt).strip()
    except EOFError:
        return default
    return val or default


def choose_category(lib, preferred=None):
    if preferred:
        lib.add_category(preferred)
        return preferred
    cats = lib.categories()
    log("")
    log("Categorieën:")
    for n, cat in enumerate(cats, 1):
        log(f"  {n:2d}) {cat}")
    log("   n) nieuwe categorie")
    while True:
        ans = ask(f"Keuze [1-{len(cats)}, n, q=stop]: ", "")
        if ans.lower() in ("q", "quit"):
            die("afgebroken")
        if ans.lower() == "n":
            name = ask("Naam nieuwe categorie: ").strip()
            if name:
                lib.add_category(name)
                return name
            continue
        if ans.isdigit() and 1 <= int(ans) <= len(cats):
            return cats[int(ans) - 1]
        if not ans:
            return cats[-1]


def fetch_meta(url):
    r = run(
        [
            "yt-dlp",
            url,
            "--no-playlist",
            "--no-warnings",
            "--print",
            "%(id)s\t%(duration)s\t%(uploader)s\t%(title)s",
        ],
        capture=True,
    )
    line = (r.stdout or "").strip().splitlines()
    if not line:
        die("kon video-informatie niet ophalen")
    vid, dur, uploader, title = (line[0].split("\t") + ["", "", "", ""])[:4]
    return {
        "id": vid,
        "duration": int(float(dur)) if dur not in ("", "NA", "None") else 0,
        "uploader": uploader if uploader != "NA" else "",
        "title": title or vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def yt_search(query, limit):
    r = run(
        [
            "yt-dlp",
            f"ytsearch{limit}:{query}",
            "--flat-playlist",
            "--no-warnings",
            "--print",
            "%(id)s\t%(duration)s\t%(uploader)s\t%(title)s",
        ],
        capture=True,
    )
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        vid, dur, uploader, title = parts[:4]
        out.append(
            {
                "id": vid,
                "duration": int(float(dur)) if dur not in ("", "NA", "None") else 0,
                "uploader": "" if uploader == "NA" else uploader,
                "title": title or vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        )
    return out


def probe(path):
    r = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture=True,
    )
    streams = json.loads(r.stdout or "{}").get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "vcodec": video.get("codec_name", ""),
        "acodec": audio.get("codec_name", ""),
        "height": int(video.get("height") or 0),
        "width": int(video.get("width") or 0),
    }


def download(url, height, workdir):
    fmt = (
        f"bv*[vcodec^=avc1][height<={height}]+ba[acodec^=mp4a]/"
        f"bv*[vcodec^=avc1][height<={height}]+ba/"
        f"bv*[height<={height}]+ba/b[height<={height}]/b"
    )
    run(
        [
            "yt-dlp",
            url,
            "-f",
            fmt,
            "--no-playlist",
            "--no-mtime",
            "--newline",
            "--no-warnings",
            "--no-part",
            "-o",
            str(Path(workdir) / "source.%(ext)s"),
        ]
    )
    files = sorted(
        [p for p in Path(workdir).iterdir() if p.is_file() and not p.name.endswith(".jpg")],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not files:
        die("download mislukt")
    return files[0]


def transcode(src, dst, height, crf):
    info = probe(src)
    copyable = (
        info["vcodec"] == "h264"
        and info["height"] <= height
        and info["acodec"] in ("aac", "")
    )
    if copyable:
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(dst),
            ]
        )
        return info
    vf = []
    if info["height"] > height:
        vf += ["-vf", f"scale=-2:{height}"]
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            *vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level",
            "4.0",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(dst),
        ]
    )
    return info


def grab_thumb(vid, dest):
    for name in ("hqdefault", "mqdefault"):
        url = f"https://i.ytimg.com/vi/{vid}/{name}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = resp.read()
            if len(data) > 1000:
                dest.write_bytes(data)
                return True
        except Exception:
            continue
    return False


def grab_frame(src, dest, duration=0):
    ss = max(1, min(int((duration or 10) * 0.1), max(1, int(duration or 10) - 1)))
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(ss), "-i", str(src),
         "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(dest)],
        capture_output=True,
    )
    return r.returncode == 0 and dest.is_file()


def ensure_video_poster(root, ident):
    poster_rel = Path("thumbs") / f"{ident}-video.jpg"
    poster = root / poster_rel
    if poster.is_file():
        return str(poster_rel)
    src = root / "videos" / "Nieuws" / f"{ident}.mp4"
    if not src.is_file():
        return ""
    if grab_frame(src, poster):
        return str(poster_rel)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
         "-vf", "scale=480:-2", "-q:v", "4", str(poster)],
        capture_output=True,
    )
    return str(poster_rel) if r.returncode == 0 and poster.is_file() else ""


def save_meta(lib, meta, category, args):
    if any(i["id"] == meta["id"] for i in lib.items):
        return None
    quality = getattr(args, "quality", 720)
    lib.add_category(category)
    cat_dir = lib.root / "videos" / safe_name(category)
    cat_dir.mkdir(parents=True, exist_ok=True)

    stem = slug(meta["title"])
    target = cat_dir / f"{stem}.mp4"
    if target.exists():
        target = cat_dir / f"{stem} [{meta['id']}].mp4"

    try:
        with tempfile.TemporaryDirectory(dir=lib.root) as tmp:
            log(f"Downloaden (max {quality}p): {meta['title']}")
            src = download(meta["url"], quality, tmp)
            info = transcode(src, target, quality, getattr(args, "crf", 23))
    except Exception as exc:
        log(f"Overslagen (download mislukt): {meta['title'][:60]} — {exc}")
        target.unlink(missing_ok=True)
        return None

    thumb = lib.root / "thumbs" / f"{meta['id']}.jpg"
    if not thumb.exists():
        grab_thumb(meta["id"], thumb)

    item = {
        "id": meta["id"],
        "title": meta["title"],
        "uploader": meta["uploader"],
        "url": meta["url"],
        "category": category,
        "file": str(target.relative_to(lib.root)),
        "thumb": str(thumb.relative_to(lib.root)) if thumb.exists() else "",
        "duration": meta["duration"],
        "size": target.stat().st_size,
        "codec": info.get("vcodec", ""),
        "height": info.get("height", 0),
        "added": datetime.now().strftime("%Y-%m-%d"),
    }
    lib.items.append(item)
    lib.save()
    lib.index_html()
    log(f"Opgeslagen: {item['file']} ({human(item['size'])})")
    return item


def cmd_add(lib, args):
    require("yt-dlp", "ffmpeg", "ffprobe")
    query = " ".join(args.query).strip()
    if not query:
        die("geef een zoekterm of URL op")

    if re.match(r"^(https?://|www\.)", query, re.I):
        meta = fetch_meta(query)
        log(f"Gekozen: {meta['title']}")
    else:
        log(f"Zoeken naar '{query}'...")
        results = yt_search(query, args.limit)
        if not results:
            die("geen resultaten")
        for n, r in enumerate(results, 1):
            log(f"  {n:2d}) [{human_dur(r['duration']):>7}] {r['title']}  — {r['uploader']}")
        pick = ask(f"Kies [1-{len(results)}, q=stop]: ")
        if pick.lower() in ("q", "quit"):
            die("afgebroken")
        if not pick.isdigit() or not (1 <= int(pick) <= len(results)):
            die("ongeldige keuze")
        meta = results[int(pick) - 1]

    category = choose_category(lib, args.category)
    item = save_meta(lib, meta, category, args)
    if not item:
        die("deze video zit al in de bibliotheek")

    used = lib.size()
    log(f"Bibliotheek: {human(used)} van {human(args.budget)} ({used / args.budget * 100:.0f}%)")
    emit({"ok": True, "item": item, "bytes": used, "budget": args.budget})

    if used >= args.budget and not JSON_MODE:
        log("")
        log("De bibliotheek is vol genoeg voor een dvd.")
        if getattr(args, "yes", False) or ask("Nu branden? [j/N]: ").lower() in ("j", "ja", "y", "yes"):
            burn(lib, args, mode=getattr(args, "disc_mode", "data"))


def news_plugin():
    override = os.environ.get("TUBE2DVD_NEWS")
    candidates = ([Path(override)] if override else []) + NEWS_PLUGIN_CANDIDATES
    found = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if not found:
        found = which("tweakers-nieuws")
    if not found:
        die("tweakers-nieuws plugin niet gevonden (zet TUBE2DVD_NEWS=/pad/tweakers-nieuws)")
    return str(found)


def cmd_note(lib, args):
    text = " ".join(args.text).strip()
    if not text:
        die("geef tekst op")
    title = str(getattr(args, "title", "") or "").strip()
    ident = "note-" + hashlib.sha1(text.encode()).hexdigest()[:12]
    if any(i["id"] == ident for i in lib.items):
        die("deze tekst zit al in de bibliotheek")
    category = getattr(args, "category", None) or TEXT_CATEGORY
    image = ""
    upload = getattr(args, "image", None)
    if upload:
        if isinstance(upload, tuple):
            filename, payload = upload
        else:
            filename, payload = str(upload), Path(upload).read_bytes()
        ext = Path(filename or "").suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        rel = Path("thumbs") / f"{ident}{ext}"
        dst = lib.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(payload)
        image = str(rel)
    item = {
        "id": ident,
        "title": title or wrap_lines(text, 42, 1)[0],
        "kind": "text",
        "body": text,
        "image": image,
        "source": getattr(args, "source", "") or "Handmatig",
        "category": category,
        "file": "",
        "thumb": "",
        "url": "",
        "duration": getattr(args, "slidesec", 8),
        "size": 0,
        "added": datetime.now().strftime("%Y-%m-%d"),
    }
    lib.items.append(item)
    lib.add_category(category)
    lib.save()
    lib.index_html()
    log(f"Tekst toegevoegd aan '{category}': {item['title'][:60]}")
    emit({"ok": True, "item": item})
    return item


def cmd_import(lib, args):
    require("ffmpeg", "ffprobe")
    category = getattr(args, "category", None) or "Overig"
    lib.add_category(category)
    cat_dir = lib.root / "videos" / safe_name(category)
    cat_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for name in args.files:
        src = Path(name).expanduser()
        if not src.is_file():
            die(f"bestand niet gevonden: {name}")
        title = getattr(args, "title", "") or src.stem.replace("_", " ").strip()
        stem = slug(title)
        dst = cat_dir / f"{stem}{src.suffix.lower()}"
        n = 2
        while dst.exists():
            dst = cat_dir / f"{stem} [{n}]{src.suffix.lower()}"
            n += 1
        shutil.copy2(src, dst)
        info = probe(dst)
        r = run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(dst)],
            capture=True,
        )
        duration = int(float(r.stdout.strip() or 0))
        ident = "file-" + hashlib.sha1(str(dst.relative_to(lib.root)).encode()).hexdigest()[:12]
        thumb = lib.root / "thumbs" / f"{ident}.jpg"
        grab_frame(dst, thumb, duration)
        item = {
            "id": ident,
            "title": title,
            "uploader": "",
            "url": "",
            "category": category,
            "file": str(dst.relative_to(lib.root)),
            "thumb": str(thumb.relative_to(lib.root)) if thumb.is_file() else "",
            "duration": duration,
            "size": dst.stat().st_size,
            "codec": info.get("vcodec", ""),
            "height": info.get("height", 0),
            "added": datetime.now().strftime("%Y-%m-%d"),
        }
        lib.items.append(item)
        added.append(item)
        log(f"Geïmporteerd: {item['file']} ({human(item['size'])})")
    lib.save()
    lib.index_html()
    emit({"ok": True, "imported": len(added), "items": added})
    return added


def news_source(url):
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    names = {
        "tweakers.net": "Tweakers",
        "nu.nl": "nu.nl",
        "omarchy.us": "Omarchy",
        "feeds.feedburner.com": "Feedburner",
        "telegraaf.nl": "De Telegraaf",
        "scientias.nl": "Scientias",
    }
    for domain, name in names.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host.split(".")[0].title() or "Onbekend"


def fetch_article(url):
    r = subprocess.run(
        [news_plugin(), "article", url], text=True, capture_output=True, timeout=120
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not data.get("ok"):
        return None
    blocks = []
    for b in data.get("blocks", []):
        text = str(b.get("x") or "").strip()
        if not text or text.startswith("```"):
            continue
        blocks.append({"t": str(b.get("t") or "p"), "x": text})
    return {
        "title": str(data.get("title") or ""),
        "author": str(data.get("author") or ""),
        "date": str(data.get("date") or ""),
        "reacties": str(data.get("reacties") or ""),
        "source": news_source(url),
        "blocks": blocks,
    }


def fetch_article_image(url, ident, root):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; tube2dvd)"}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=20
        ) as r:
            html = r.read(2_000_000).decode("utf-8", "replace")
    except Exception:
        return ""
    names = r"og:image|twitter:image|twitter:image:src"
    pats = [
        r'<meta[^>]+(?:property|name)=["\'](?:' + names + r')["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:' + names + r')["\']',
    ]
    picks = []
    for pat in pats:
        picks += re.findall(pat, html, re.I)
    if not picks:
        return ""
    img_url = urllib.parse.urljoin(url, picks[0])
    try:
        with urllib.request.urlopen(
            urllib.request.Request(img_url, headers=headers), timeout=20
        ) as r:
            data = r.read(8_000_000)
            ctype = r.headers.get_content_type()
    except Exception:
        return ""
    if not ctype.startswith("image/") or len(data) < 1000:
        return ""
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ctype, ".img")
    rel = Path("thumbs") / f"{ident}{ext}"
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return str(rel)


def fetch_article_video(url, ident, root):
    rel = Path("videos") / "Nieuws" / f"{ident}.mp4"
    poster_rel = Path("thumbs") / f"{ident}-video.jpg"
    dst = root / rel
    poster = str(poster_rel) if (root / poster_rel).is_file() else ""
    if dst.is_file() and dst.stat().st_size > 10000:
        return {"path": str(rel), "size": dst.stat().st_size, "title": ident,
                "poster": ensure_video_poster(root, ident) or poster}
    try:
        probe = subprocess.run(
            ["yt-dlp", "--simulate", "--dump-single-json", "--no-warnings", url],
            text=True, capture_output=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if probe.returncode != 0:
        return None
    try:
        info = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not (info.get("id") and info.get("duration") and info.get("formats")):
        return None
    if info.get("thumbnail") and not poster:
        try:
            with urllib.request.urlopen(info["thumbnail"], timeout=15) as r:
                blob = r.read(3_000_000)
            if len(blob) > 1000:
                (root / poster_rel).parent.mkdir(parents=True, exist_ok=True)
                (root / poster_rel).write_bytes(blob)
                poster = str(poster_rel)
        except Exception:
            pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(ident + ".download.%(ext)s")
    for old in dst.parent.glob(ident + ".download.*"):
        old.unlink()
    try:
        r = subprocess.run(
            ["yt-dlp", "-f", "best[ext=mp4][height<=720]/best[ext=mp4]/best",
             "--merge-output-format", "mp4", "--max-filesize", "150M",
             "--no-warnings", "-o", str(tmp), url],
            text=True, capture_output=True, timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError):
        r = None
    files = [p for p in dst.parent.glob(ident + ".download.*") if p.stat().st_size > 10000]
    if r is None or r.returncode != 0 or not files:
        for p in dst.parent.glob(ident + ".download.*"):
            p.unlink()
        return None
    got = max(files, key=lambda p: p.stat().st_size)
    if got.suffix.lower() != ".mp4":
        converted = dst.with_suffix(".mp4")
        rr = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(got), "-c", "copy", str(converted)],
            capture_output=True,
        )
        got.unlink()
        if rr.returncode != 0 or not converted.is_file():
            return None
        got = converted
    if got != dst:
        got.replace(dst)
    return {"path": str(rel), "size": dst.stat().st_size, "title": str(info.get("title") or ""),
            "poster": ensure_video_poster(root, ident) or poster}


def news_sources():
    r = subprocess.run([news_plugin(), "sources"], text=True, capture_output=True)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []


def news_indices():
    sources = news_sources()
    if not sources:
        return [0]
    return list(range(min(3, len(sources))))


def cmd_news(lib, args):
    urls = getattr(args, "urls", None)
    if urls:
        rows = [{"url": u} for u in urls]
    else:
        cmd = [news_plugin(), "list", "--count", str(args.count), "--force"]
        if getattr(args, "source", 0):
            cmd += ["--source", str(args.source)]
        r = subprocess.run(cmd, text=True, capture_output=True)
        if r.returncode != 0:
            die(f"nieuws ophalen mislukt: {r.stderr.strip()}")
        try:
            rows = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            die("nieuwsplugin gaf geen geldige json terug")

    if not urls and not getattr(args, "source", 0) and getattr(args, "all_sources", False):
        seen_urls = {x.get("url") for x in rows}
        share = max(1, args.count // max(1, len(news_indices())))
        for src in news_indices():
            r2 = subprocess.run(
                [news_plugin(), "list", "--count", str(share), "--force", "--source", str(src)],
                text=True, capture_output=True,
            )
            if r2.returncode != 0:
                continue
            try:
                extra = json.loads(r2.stdout or "[]")
            except json.JSONDecodeError:
                continue
            for x in extra:
                if x.get("url") and x["url"] not in seen_urls:
                    rows.append(x)
                    seen_urls.add(x["url"])

    imported = []
    refreshed = 0
    for i in lib.items:
        if i.get("kind") != "news" or not i.get("url"):
            continue
        changed = False
        if not i.get("source"):
            i["source"] = news_source(i["url"])
            changed = True
        if not getattr(args, "no_body", False) and not i.get("image"):
            image = fetch_article_image(i["url"], i["id"], lib.root)
            if image:
                i["image"] = image
                changed = True
        if not getattr(args, "no_body", False) and "video" not in i:
            vid = fetch_article_video(i["url"], i["id"], lib.root)
            if vid:
                i["video"] = vid["path"]
                i["video_size"] = vid["size"]
                i["video_title"] = vid["title"]
                i["video_poster"] = vid.get("poster") or ""
                i["size"] = vid["size"]
            else:
                i["video"] = ""
            changed = True
        if changed:
            refreshed += 1
            lib.save()
    by_id = {i["id"]: i for i in lib.items}
    for row in rows:
        url = str(row.get("url") or "")
        if not url:
            continue
        ident = "news-" + hashlib.sha1(url.encode()).hexdigest()[:12]
        existing = by_id.get(ident)
        if existing and not getattr(args, "refresh", False):
            continue
        article = None
        image = ""
        video = None
        if not getattr(args, "no_body", False):
            video = fetch_article_video(url, ident, lib.root)
        if not getattr(args, "no_body", False) and (not existing or not existing.get("article")):
            log(f"Volledig artikel ophalen: {str(row.get('title') or url)[:60]}")
            article = fetch_article(url)
        if not getattr(args, "no_body", False) and not existing:
            image = fetch_article_image(url, ident, lib.root)
        if existing:
            changed = False
            if article:
                existing["article"] = article
                refreshed += 1
                changed = True
            if image:
                existing["image"] = image
                changed = True
            if not existing.get("source"):
                existing["source"] = news_source(url)
                changed = True
            if changed:
                lib.save()
            continue
        item = {
            "id": ident,
            "title": str(row.get("title") or url),
            "lead": str(row.get("lead") or ""),
            "date": str(row.get("date") or ""),
            "source": news_source(url),
            "url": url,
            "article": article,
            "image": image,
            "video": video["path"] if video else "",
            "video_size": video["size"] if video else 0,
            "video_title": video["title"] if video else "",
            "video_poster": video.get("poster") or "" if video else "",
            "category": args.category,
            "kind": "news",
            "file": "",
            "thumb": "",
            "duration": getattr(args, "slidesec", 8),
            "size": video["size"] if video else 0,
            "added": datetime.now().strftime("%Y-%m-%d"),
        }
        lib.items.append(item)
        imported.append(item)

    if imported or refreshed:
        lib.add_category(args.category)
        lib.save()
        lib.index_html()
    log(f"{len(imported)} nieuwsberichten toegevoegd, {refreshed} artikelen ververst")
    emit({"ok": True, "imported": len(imported), "refreshed": refreshed, "items": imported})
    return imported


def news_browser_rows():
    out = []
    for src in news_indices():
        sources = news_sources()
        name = sources[src].get("name") if src < len(sources) else f"Bron {src + 1}"
        r = subprocess.run(
            [news_plugin(), "list", "--count", "12", "--force", "--source", str(src)],
            text=True, capture_output=True,
        )
        try:
            rows = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            rows = []
        out.append({"index": src, "name": name or f"Bron {src + 1}", "rows": rows})
    return out


def news_browser_html(lib):
    parts = []
    for group in news_browser_rows():
        items = ""
        for row in group["rows"]:
            url = str(row.get("url") or "")
            if not url:
                continue
            title = str(row.get("title") or url)
            q = urllib.parse.urlencode({"url": url, "source": group["index"]})
            items += (
                f'<li><a href="/news-preview?{q}">{escape(title)}</a>'
                f'<small>{escape(str(row.get("date") or ""))}</small></li>'
            )
        parts.append(
            f'<details class="news-source"><summary>{escape(group["name"])} '
            f'<span>{len(group["rows"])}</span></summary>'
            f'<ul>{items or "<li>Geen artikelen.</li>"}</ul></details>'
        )
    return "".join(parts) or "<p><small>Geen nieuwsbronnen beschikbaar.</small></p>"


def news_browser_page(lib):
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
<title>Nieuws per bron</title><style>{WEB_CSS}
.news-source{{margin:8px 0;border:1px solid #d8c39a;border-radius:4px;background:#fffaf0}}
.news-source>summary{{cursor:pointer;padding:10px 12px;font-variant:small-caps;color:#8e3b2f;
 list-style:none}}
.news-source>summary::-webkit-details-marker{{display:none}}
.news-source>summary:after{{content:" \\25B6";float:right;color:#9c4032}}
.news-source[open]>summary:after{{content:" \\25BC"}}
.news-source>summary span{{color:#8a7558;font-size:12px;margin-left:6px}}
.news-source ul li{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px}}
.news-source ul li small{{color:#8a7558;font-size:11px}}
</style>
<div class="wrap" style="padding:12px"><h2 style="font-size:17px">Eerste drie nieuwsbronnen</h2>
{news_browser_html(lib)}</div>"""


def news_preview_page(lib, url, message=""):
    article = fetch_article(url)
    article = article or {
        "title": url, "author": "", "date": "", "reacties": "", "source": news_source(url),
        "blocks": [],
    }
    def render(bs):
        out = ""
        for b in bs:
            text = escape(str(b.get("x") or ""))
            t = b.get("t")
            if t in ("h", "h2", "h3"):
                out += f"<h3>{text}</h3>"
            elif t in ("li", "bullet"):
                out += f"<p>&bull; {text}</p>"
            else:
                out += f"<p>{text}</p>"
        return out

    all_blocks = article.get("blocks", [])
    lead = [b for b in all_blocks if b.get("t") == "lead"]
    body_blocks = [b for b in all_blocks if b.get("t") != "lead"] or all_blocks
    intro = render(lead) or f'<p>{escape(str(article.get("lead") or ""))}</p>'
    blocks = (
        f'<div class="intro">{intro}</div>'
        '<details class="article"><summary>Lees het volledige artikel</summary>'
        '<div class="article-body">' + render(body_blocks) + "</div></details>"
    )
    ident = "news-" + hashlib.sha1(url.encode()).hexdigest()[:12]
    added = any(i["id"] == ident for i in lib.items)
    msg = f'<p class="msg">{escape(message)}</p>' if message else ""
    state = '<p><small>Dit artikel staat al in je dvd.</small></p>' if added else ""
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
<title>Nieuws bekijken</title><style>{WEB_CSS}
.news-source{{margin:8px 0;border:1px solid #d8c39a;border-radius:4px;background:#fffaf0}}
.news-source>summary{{cursor:pointer;padding:10px 12px;font-variant:small-caps;color:#8e3b2f}}
.news-source>summary span{{float:right;color:#8a7558}}
.news-source ul li{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px}}
.news-source ul li small{{color:#8a7558;font-size:11px}}
.preview-body p{{font-size:14px;line-height:1.6;margin:0 0 12px}}
.preview-body .intro p{{font-size:15px;line-height:1.6;color:#554331}}
.preview-body .article{{margin-top:12px}}
.preview-body .article summary{{cursor:pointer;list-style:none;font-size:13px;font-variant:small-caps;color:#8e3b2f}}
.preview-body .article summary::-webkit-details-marker{{display:none}}
.preview-body .article summary:after{{content:" \\25B6";font-size:11px}}
.preview-body .article[open] summary:after{{content:" \\25BC"}}
.preview-body .article-body{{max-height:65vh;overflow-y:auto;overscroll-behavior:contain;padding:0 12px 4px}}
.navtop{{position:sticky;top:0;z-index:2;background:#efe2c6;padding:8px 0 10px;margin:0 0 6px;
 border-bottom:1px solid #d8c39a}}
.navtop a{{display:inline-block;padding:8px 14px;border:1px solid #d8c39a;border-radius:14px 5px 14px 5px;
 background:#fffaf0;text-decoration:none;font-variant:small-caps;color:#8e3b2f}}
</style>
<div class="wrap"><div class="navtop"><a href="/news-browser">&larr; Terug naar nieuws per bron</a></div>
<header><h1>Artikel bekijken</h1>
<p>{escape(article.get("source") or news_source(url))} · {escape(article.get("date") or "")}</p></header>
<div class="card"><h2>{escape(article.get("title") or url)}</h2>
<p><small>{escape(article.get("author") or "")} {escape(article.get("reacties") or "")}</small></p>
<div class="preview-body">{blocks}</div>
<p><a href="{escape(url)}" target="_blank" rel="noopener">origineel artikel</a></p>
{msg}{state}
<form method="post" action="/news-add-one">
<input type="hidden" name="url" value="{escape(url)}">
<label>Categorie</label>{cat_select("category", NEWS_CATEGORY, lib)}
<button type="submit"{' disabled' if added else ''}>Toevoegen aan dvd</button></form>
<p><a href="/news-browser">&larr; terug naar nieuws per bron</a> · <a href="/">plakboek</a></p></div></div>"""


def parse_specs(specs):
    out = []
    for spec in specs:
        if "=" not in spec:
            die(f"gebruik 'Categorie=zoekterm', bijv. 'Katten=funny cat videos': {spec}")
        cat, query = spec.split("=", 1)
        cat, query = cat.strip(), query.strip()
        if not cat or not query:
            die(f"lege categorie of zoekterm in: {spec}")
        out.append((cat, query))
    if not out:
        die("geen categorieën opgegeven")
    return out


def cmd_collect(lib, args, quiet_summary=False):
    require("yt-dlp", "ffmpeg", "ffprobe")
    specs = parse_specs(args.spec)
    target = parse_size(args.target)
    seen = {i["id"] for i in lib.items}
    results = {}
    for cat, query in specs:
        log(f"Zoeken naar '{query}' [{cat}]...")
        results[cat] = yt_search(query, args.limit)

    added = []
    for rank in range(args.limit):
        if lib.size() >= target:
            break
        for cat, _ in specs:
            if lib.size() >= target or rank >= len(results[cat]):
                continue
            meta = results[cat][rank]
            if meta["id"] in seen:
                continue
            if meta["duration"] and meta["duration"] > args.max_duration:
                continue
            seen.add(meta["id"])
            item = save_meta(lib, meta, cat, args)
            if item:
                added.append(item)
                progress("Downloaden", lib.size(), target)

    by_cat = {}
    for item in added:
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
    used = lib.size()
    if used >= target:
        notify("tube2dvd: map vol", f"{human(used)} in {lib.root} — DVD gebrand kan worden")
    summary = {
        "ok": True,
        "added": len(added),
        "bytes": used,
        "target": target,
        "by_category": by_cat,
        "items": added,
    }
    if not quiet_summary:
        log(f"{len(added)} video's toegevoegd ({human(used)} van {human(target)})")
        emit(summary)
    return summary


def cmd_dvd(lib, args):
    summary = cmd_collect(lib, args, quiet_summary=True)
    if args.news:
        news_args = argparse.Namespace(
            count=args.news, source=0, all_sources=True, category=args.news_category,
            slidesec=args.slidesec, no_body=getattr(args, "no_body", False),
        )
        news = cmd_news(lib, news_args)
        summary["news"] = len(news)

    label = args.label or f"TUBE2DVD_{(len(lib.discs) + 1):02d}"
    work = lib.root / "build"
    iso = work / f"{label}.iso"
    burn_args = argparse.Namespace(
        mode="video",
        tv=args.tv,
        label=label,
        device=args.device,
        all=False,
        image=None if args.burn else str(iso),
        dry_run=not args.burn,
        keep_image=True,
        yes=args.yes,
    )
    collected_bytes = summary["bytes"]
    result = burn(lib, burn_args, mode="video")
    summary.update(result)
    summary["bytes"] = collected_bytes
    summary["iso_bytes"] = result["bytes"]
    summary["label"] = label
    log(f"DVD klaar: {result['iso']}")
    emit(summary)
    return summary


def cmd_list(lib, args):
    if not lib.items:
        log("bibliotheek is leeg")
        emit({"ok": True, "items": []})
        return
    for cat in lib.categories():
        rows = [i for i in lib.items if i["category"] == cat]
        if not rows:
            continue
        log(f"\n{cat}  ({len(rows)} video's, {human(sum(i['size'] for i in rows))})")
        for i in rows:
            mark = "x" if i.get("disc") else " "
            log(f"  [{mark}] {human_dur(i['duration']):>7}  {human(i['size']):>7}  {i['title']}  ({i['id']})")
    log("\n[x] = al op een dvd gebrand")
    emit({
        "ok": True,
        "items": [
            {
                "id": i["id"], "title": i["title"], "category": i["category"],
                "kind": i.get("kind", "video"), "bytes": i["size"],
                "duration": i.get("duration", 0), "disc": i.get("disc"), "url": i.get("url", ""),
            }
            for i in lib.items
        ],
        "pending": [
            i["id"] for i in lib.items if not i.get("disc")
        ],
    })


def cmd_status(lib, args):
    used = lib.size()
    log(f"Map:   {lib.root}")
    log(f"Categorieën: {lib.cat_path}")
    log("")
    if not lib.items:
        log("Nog geen video's.")
    else:
        log(f"{'Categorie':<24}{'#':>4}{'Grootte':>12}")
        for cat in lib.categories():
            rows = [i for i in lib.items if i["category"] == cat]
            if not rows:
                continue
            log(f"{cat[:23]:<24}{len(rows):>4}{human(sum(i['size'] for i in rows)):>12}")
        log("-" * 40)
        log(f"{'Totaal':<24}{len(lib.items):>4}{human(used):>12}")
        log(f"{'Budget':<24}{'':>4}{human(args.budget):>12}  ({used / args.budget * 100:.0f}% vol)")
        if used >= args.budget:
            log("\nKlaar om te branden: tube2dvd burn")
    if lib.discs:
        log("\nGebrande schijven:")
        for d in lib.discs:
            log(f"  {d['label']:<18} {d['date']}  {d['mode']:<6} {len(d['items']):>3} video's  {human(d['bytes'])}")
    emit({
        "ok": True,
        "dir": str(lib.root),
        "budget": args.budget,
        "bytes": used,
        "items": len(lib.items),
        "pending": sum(1 for i in lib.items if not i.get("disc")),
        "categories": [
            {
                "name": cat,
                "count": len([i for i in lib.items if i["category"] == cat]),
                "bytes": sum(i["size"] for i in lib.items if i["category"] == cat),
            }
            for cat in lib.categories()
            if any(i["category"] == cat for i in lib.items)
        ],
        "discs": lib.discs,
    })


def cmd_remove(lib, args):
    if args.burned:
        rows = [i for i in lib.items if i.get("disc")]
        if not rows:
            log("geen gebrande video's gevonden")
            return
        for i in rows:
            lib.items.remove(i)
        lib.save()
        lib.index_html()
        log(f"{len(rows)} items uit de bibliotheek gehaald (bestanden blijven staan)")
        return
    if not args.what:
        die("geef een titel, id of --burned op")
    item = lib.find(args.what)
    lib.items.remove(item)
    if not args.keep and item.get("file") and item.get("kind") != "news":
        (lib.root / item["file"]).unlink(missing_ok=True)
        if item.get("thumb"):
            (lib.root / item["thumb"]).unlink(missing_ok=True)
    lib.save()
    lib.index_html()
    log(f"verwijderd: {item['title']}")


def cmd_clean(lib, args):
    rows = [i for i in lib.items if i.get("disc")]
    if not rows:
        log("niets op te ruimen")
        return
    freed = 0
    for i in rows:
        p = lib.root / i["file"] if i.get("file") else None
        if p and p.exists():
            freed += p.stat().st_size
            p.unlink()
        lib.items.remove(i)
    lib.save()
    lib.index_html()
    log(f"{len(rows)} video's verwijderd, {human(freed)} vrijgemaakt")


def cmd_menu(lib, args):
    lib.index_html()
    log(f"menu: {lib.root / 'index.html'}")
    emit({"ok": True, "html": str(lib.root / "index.html"), "style": STYLE})


def write_html(lib, items, out):
    style = STYLE
    cats = [c for c in lib.categories() if any(i["category"] == c for i in items)]
    total = sum(i["size"] for i in items)
    parts = [
        "<!doctype html>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Cache-Control" content="no-store">',
        f"<title>{escape(dvd_name(lib) or 'tube2dvd')} — {len(items)} video's</title>",
        """<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0f151d;color:#e8eef6;font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 24px 8px}
h1{margin:0;font-size:26px;letter-spacing:-.02em}
header p{margin:6px 0 0;color:#8fa6bd;font-size:14px}
h2{margin:32px 24px 12px;font-size:18px;color:#bcd3ea;border-bottom:1px solid #22303f;padding-bottom:6px}
h2 span{float:right;font-weight:400;font-size:13px;color:#7f95ab}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px;padding:0 24px 32px}
figure{margin:0;background:#151e28;border:1px solid #22303f;border-radius:10px;overflow:hidden}
video{width:100%;aspect-ratio:16/9;background:#000;display:block}
figcaption{padding:10px 12px 12px;font-size:14px;line-height:1.35}
figcaption a{color:#e8eef6;text-decoration:none}
figcaption a:hover{color:#7fc4ff}
small{color:#8fa6bd;font-size:12px;display:block;margin-top:4px}
footer{padding:8px 24px 40px;color:#5f7488;font-size:12px}
body.scrapbook{color:#3b2b22;color-scheme:light;font-family:"DejaVu Serif",Georgia,serif;
 background-color:#efe2c6;background-image:radial-gradient(#cdbb96 1px,transparent 1px),linear-gradient(115deg,#f6ecd6,#e8d8b6);
 background-size:22px 22px,100% 100%}
.scrapbook header{padding:34px 24px 4px}
.scrapbook h1{font-size:34px;color:#8e3b2f;transform:rotate(-1deg);transform-origin:left center}
.scrapbook header p{color:#6d5842;font-size:15px}
.scrapbook h2{display:inline-block;margin:34px 24px 14px;padding:7px 18px;border:0;border-radius:14px 5px 14px 5px;
 background:var(--h2,#c75d4a);color:#fff8e7;box-shadow:3px 3px 0 var(--h2-shadow,#9c4032);
 transform:rotate(var(--h2-rot,-.5deg))}
.scrapbook h2 span{color:#ffe0c9;margin-left:10px}
.scrapbook .grid{gap:26px 22px}
.scrapbook figure{position:relative;background:var(--paper,#fffaf0);border:1px solid #d8c39a;border-radius:3px;
 box-shadow:3px 5px 0 rgba(90,66,38,.18);transform:rotate(var(--rot,-.8deg));overflow:visible}
.scrapbook figure:before{content:"";position:absolute;z-index:2;top:-11px;left:var(--tape-x,42%);
 width:var(--tape-w,68px);height:23px;background:var(--tape,#f0d98a);
 border-left:1px solid rgba(90,66,38,.2);border-right:1px solid rgba(90,66,38,.2);
 opacity:.9;transform:rotate(var(--tape-rot,-3deg))}
.scrapbook figure[data-clip]:before{display:none}
.scrapbook figure[data-clip]:after{content:"";position:absolute;z-index:2;top:-15px;right:22px;width:13px;height:32px;
 border:3px solid #9aa7ad;border-radius:8px;transform:rotate(9deg);box-shadow:1px 1px 0 rgba(90,66,38,.25)}
.scrapbook figure:not(.newsfig):not(.textfig){padding:6px 6px 0}
.scrapbook figure:not(.newsfig):not(.textfig) figcaption{padding:8px 6px 10px}
.scrapbook .newsfig{border-style:dashed;border-width:1px}
.scrapbook .textfig{border-radius:12px 2px 12px 2px;box-shadow:4px 5px 0 rgba(90,66,38,.2)}
.scrapbook figure video{border-radius:2px 2px 0 0;background:#2b2118}
.newsfig .news-photo{display:block;width:100%;height:130px;object-fit:cover;border-radius:2px 2px 0 0;border-bottom:1px solid #d8c39a}
.newsfig .article-photo{display:block;width:100%;max-height:180px;object-fit:cover;border-radius:3px;margin:0 0 12px}
.newsfig .article-video{margin:0 0 12px}
.newsfig .article-video video{display:block;width:100%;max-height:420px;background:#1a1512;border-radius:4px}
.newsfig .article-video small{display:block;font-size:11px;color:#8a7558;margin-top:4px}
.scrapbook figcaption a{color:#8e3b2f}
.scrapbook figcaption a:hover{color:#c75d4a}
.scrapbook small{color:#7a654e}
.scrapbook footer{color:#8a7558}
.scrapbook .newsfig figcaption{padding-top:16px}
.scrapbook .newsfig .lead{color:#554331;font-size:13px;margin:8px 0;line-height:1.5}
.scrapbook .newsfig .date{font-variant:small-caps;letter-spacing:.04em;color:#9c4032}
.scrapbook .textfig figcaption{padding:18px 16px 16px}
.scrapbook .textfig .news-photo{display:block;width:100%;height:auto;aspect-ratio:16/9;object-fit:cover;object-position:center 45%;background:#efe2c6;border:3px solid #fffaf0;border-bottom:1px solid #d8c39a;border-radius:12px 2px 0 0;box-shadow:0 2px 0 rgba(90,66,38,.18)}
.scrapbook .textfig .bodytext{color:#463527;font-size:14px;line-height:1.6;margin:10px 0;white-space:pre-wrap}
.newsfig .source{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:8px 3px 8px 3px;
 font-size:11px;font-variant:small-caps;letter-spacing:.03em;background:#c75d4a;color:#fff8e7}
.newsfig h3{margin:6px 0 0;font-size:15px;line-height:1.3}
.newsfig summary{cursor:pointer;list-style:none;margin:10px 0 0;font-size:12px;
 font-variant:small-caps;color:#8e3b2f}
.newsfig summary::-webkit-details-marker{display:none}
.newsfig summary:after{content:" \\25B6";font-size:11px}
.newsfig details[open] summary:after{content:" \\25BC"}
.newsfig .article-body{max-height:65vh;overflow-y:auto;overscroll-behavior:contain;
 padding:0 12px 4px}
.newsfig .article .meta{font-size:12px;color:#8a7558;font-variant:small-caps;margin:0 0 10px}
.newsfig .article p{margin:0 0 10px;font-size:13.5px;line-height:1.55}
.newsfig .article h4{margin:16px 0 6px;font-size:15px;color:#8e3b2f}
.newsfig .article .origin{display:block;margin-top:10px;font-size:11px;color:#9c4032}
body.classic .newsfig .source{background:#2f6f9f;color:#eaf4ff}
body.classic .newsfig summary{color:#7fc4ff}
body.classic .newsfig .article .meta{color:#8fa6bd}
body.classic .newsfig .article h4{color:#bcd3ea}
body.classic .newsfig .article .origin{color:#7fc4ff}
</style>""",
        f'<body class="{style}">',
        "<header>",
        f"<h1>{escape(dvd_name(lib) or 'tube2dvd')}</h1>"
        f"<p>{len(items)} video's · {human(total)} · {len(cats)} categorieën</p>",
        "</header>",
    ]
    for cat in cats:
        rows = [i for i in items if i["category"] == cat]
        parts.append(
            f"<h2{scrap_category_style(cat)}>{escape(cat)}"
            f"<span>{len(rows)} · {human(sum(i['size'] for i in rows))}</span></h2>"
        )
        parts.append('<div class="grid">')
        for i in rows:
            if i.get("kind") == "text":
                parts.append(
                    '<figure class="newsfig textfig"' + scrap_decor(i["id"]) + ">"
                    + (f'<img class="news-photo" src="{escape(i["image"])}" alt="">' if i.get("image") else "")
                    + '<figcaption>'
                    f'<span class="source">Tekst</span>'
                    f'<h3>{escape(i["title"])}</h3>'
                    f'<p class="bodytext">{escape(i.get("body") or "")}</p>'
                    f'<small>Bron: {escape(i.get("source") or "Handmatig")}</small>'
                    "</figcaption></figure>"
                )
                continue
            if i.get("kind") == "news":
                article = i.get("article") or {}
                meta = " · ".join(
                    x for x in (article.get("author"), article.get("reacties")) if x
                )
                body = []
                photo = ""
                title = str(i.get("title") or "")
                headline = title
                if i.get("video"):
                    vp = i.get("video_poster") or i.get("image") or ""
                    vposter = f' poster="{escape(vp)}"' if vp else ""
                    vlabel = escape(i.get("video_title") or "Video bij dit artikel")
                    photo = (
                        '<video playsinline preload="none"'
                        ' onclick="if(!this.controls){this.controls=true;this.currentTime=0;this.play().catch(function(){})}"'
                        ' onended="this.pause();this.load();this.controls=false"'
                        + vposter + '><source src="' + escape(i["video"])
                        + '" type="video/mp4"></video>'
                    )
                    headline = f'<a href="{escape(i["video"])}">{vlabel}</a>'
                    if not (i.get("image") or ""):
                        headline = f'<a href="{escape(i["video"])}">{escape(i.get("video_title") or title)}</a>'
                elif i.get("image"):
                    photo = f'<img class="news-photo" src="{escape(i["image"])}" alt="">'
                if i.get("image"):
                    body.append(f'<img class="article-photo" src="{escape(i["image"])}" alt="">')
                if meta:
                    body.append(f'<p class="meta">{escape(meta)}</p>')
                for b in article.get("blocks", []):
                    text = escape(b.get("x") or "")
                    if b.get("t") in ("h", "h2", "h3"):
                        body.append(f"<h4>{text}</h4>")
                    elif b.get("t") in ("li", "bullet"):
                        body.append(f"<p>&bull; {text}</p>")
                    else:
                        body.append(f"<p>{text}</p>")
                if not body:
                    body.append(f'<p>{escape(i.get("lead") or "")}</p>')
                body.append(
                    f'<span class="origin">Bron: {escape(article.get("source") or i.get("source") or "onbekend")} · '
                    f'<a href="{escape(i["url"])}">origineel artikel</a></span>'
                )
                parts.append(
                    '<figure class="newsfig"' + scrap_decor(i["id"]) + '>' + photo + '<figcaption>'
                    f'<span class="date">{escape(i.get("date") or "")}</span>'
                    f'<span class="source">{escape(article.get("source") or i.get("source") or "Nieuws")}</span>'
                    f'<h3>{headline}</h3>'
                    f'<p class="lead">{escape(i.get("lead") or "")}</p>'
                    '<details class="article"><summary>Lees het volledige artikel</summary>'
                    '<div class="article-body">' + "".join(body) + "</div></details>"
                    "</figcaption></figure>"
                )
                continue
            poster = f' poster="{escape(i["thumb"])}"' if i.get("thumb") else ""
            parts.append(
                "<figure" + scrap_decor(i["id"]) + ">"
                f'<video playsinline preload="none" onclick="if(!this.controls){{this.controls=true;this.currentTime=0;this.play().catch(function(){{}})}}" onended="this.pause();this.load();this.controls=false"{poster}>'
                f'<source src="{escape(i["file"])}" type="video/mp4">'
                "</video>"
                "<figcaption>"
                f'<a href="{escape(i["file"])}">{escape(i["title"])}</a>'
                f"<small>{escape(i.get('uploader') or '')} · {human_dur(i['duration'])} · {human(i['size'])}</small>"
                f'<small><a href="{escape(i["url"])}">origineel</a></small>'
                "</figcaption></figure>"
            )
        parts.append("</div>")
    parts.append(f"<footer>Gemaakt met tube2dvd · {datetime.now():%Y-%m-%d}</footer>")
    Path(out).write_text("\n".join(parts) + "\n")


def escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


SCRAP_PAPERS = ("#fffaf0", "#f7efd9", "#fdf6e3", "#f4ebd8", "#fff5e6", "#f1eadb")
SCRAP_TAPES = ("#f0d98a", "#c9d8c6", "#e6b7a8", "#b9c9d6", "#d8c3e0", "#e8d5b0")
SCRAP_HEADINGS = (
    ("#c75d4a", "#9c4032"), ("#4f7c6f", "#34564c"), ("#5c6f9c", "#3d4a6d"),
    ("#a06b3f", "#6e4829"), ("#8a5a7b", "#5e3c53"),
)


def scrap_decor(ident):
    h = hashlib.sha1(str(ident).encode()).digest()
    return (
        f' style="--rot:{(h[0] % 13) - 6}deg;'
        f'--tape-x:{10 + h[1] % 58}%;'
        f'--tape-w:{44 + h[2] % 38}px;'
        f'--tape-rot:{(h[3] % 9) - 4}deg;'
        f'--tape:{SCRAP_TAPES[h[4] % len(SCRAP_TAPES)]};'
        f'--paper:{SCRAP_PAPERS[h[5] % len(SCRAP_PAPERS)]}"'
        + (" data-clip" if h[6] % 4 == 0 else "")
    )


def scrap_category_style(name):
    h = hashlib.sha1(str(name).encode()).digest()
    bg, shadow = SCRAP_HEADINGS[h[0] % len(SCRAP_HEADINGS)]
    return f' style="--h2:{bg};--h2-shadow:{shadow};--h2-rot:{(h[1] % 9) - 4}deg"'


def stage_data(lib, items, staging):
    def stage_rel(rel_text):
        if not rel_text:
            return
        rel = Path(rel_text)
        src = lib.root / rel
        if not src.is_file():
            return
        dst = Path(staging) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            return
        try:
            os.link(src, dst)
        except OSError:
            if not dst.exists():
                shutil.copy2(src, dst)

    for i in items:
        stage_rel(i.get("thumb"))
        stage_rel(i.get("image"))
        stage_rel(i.get("video"))
        if i.get("kind") in ("news", "text"):
            continue
        stage_rel(i["file"])
    news = [i for i in items if i.get("kind") == "news"]
    if news:
        (Path(staging) / "nieuws.json").write_text(
            json.dumps(news, indent=1, ensure_ascii=False) + "\n"
        )
    texts = [i for i in items if i.get("kind") == "text"]
    if texts:
        (Path(staging) / "teksten.json").write_text(
            json.dumps(texts, indent=1, ensure_ascii=False) + "\n"
        )
    write_html(lib, items, Path(staging) / "index.html")


def make_iso(tree, iso, label, video=False):
    if which("mkisofs"):
        base = ["mkisofs"]
    elif which("genisoimage"):
        base = ["genisoimage"]
    elif which("xorriso"):
        base = ["xorriso", "-as", "mkisofs"]
    else:
        die("installeer cdrtools (mkisofs) of libisoburn (xorriso)")
    args = base + ["-f", "-joliet", "-joliet-long", "-V", label[:32], "-o", str(iso)]
    args += ["-dvd-video"] if video else ["-udf", "-iso-level", "3"]
    args.append(str(tree))
    log(f"ISO bouwen: {iso}")
    run(args)


def burn_iso(iso, device):
    log(f"Branden naar {device}...")
    if which("growisofs"):
        run(["growisofs", "-dvd-compat", "-Z", f"{device}={iso}"])
    elif which("xorriso"):
        run(["xorriso", "-as", "cdrecord", "-v", "-eject", f"dev={device}", str(iso)])
    elif which("wodim"):
        run(["wodim", "-v", "-eject", f"dev={device}", str(iso)])
    else:
        die("installeer dvd+rw-tools (growisofs) of libisoburn (xorriso)")


def burn(lib, args, mode=None):
    mode = mode or getattr(args, "mode", "data")
    items = lib.items if getattr(args, "all", False) else [i for i in lib.items if not i.get("disc")]
    if not items:
        if lib.items:
            die("alle video's staan al op een dvd (gebruik --all of voeg nieuwe toe)")
        die("bibliotheek is leeg")
    label = getattr(args, "label", None) or f"TUBE2DVD_{(len(lib.discs) + 1):02d}"
    image = getattr(args, "image", None)
    work = lib.root / "build"
    work.mkdir(exist_ok=True)
    iso = Path(image).expanduser() if image else work / f"{label}.iso"
    iso.parent.mkdir(parents=True, exist_ok=True)

    if mode == "data":
        used = sum(i["size"] for i in items)
        if used > DISC_BYTES:
            die(f"te groot voor één dvd: {human(used)}")
        log(f"Datadisc met {len(items)} video's ({human(used)})")
        staging = work / "stage-data"
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError as exc:
                die(f"kan oude staging-map niet opruimen: {staging} ({exc})")
        staging.mkdir(parents=True)
        stage_data(lib, items, staging)
        make_iso(staging, iso, label)
    else:
        require("ffmpeg", "ffprobe", "dvdauthor", "spumux")
        build_dvd_video(lib, items, work, iso, label, args)

    size = iso.stat().st_size
    log(f"ISO klaar: {iso} ({human(size)})")
    result = {"ok": True, "iso": str(iso), "bytes": size, "mode": mode, "label": label, "burned": False}
    if image:
        return result
    if getattr(args, "dry_run", False):
        log("dry-run: niet gebrand")
        return result

    device = getattr(args, "device", None) or device_path()
    if not getattr(args, "yes", False):
        if ask(f"DVD+RW in {device}? Branden [j/N]: ").lower() not in ("j", "ja", "y", "yes"):
            die("afgebroken")
    burn_iso(iso, device)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lib.discs.append(
        {
            "label": label,
            "mode": mode,
            "date": stamp,
            "items": [i["id"] for i in items],
            "bytes": size,
        }
    )
    for i in items:
        i["disc"] = label
    lib.save_discs()
    lib.save()
    lib.index_html()
    if not getattr(args, "keep_image", False):
        iso.unlink(missing_ok=True)
        shutil.rmtree(work / "stage-data", ignore_errors=True)
    log("Klaar. Tip: tube2dvd clean verwijdert de gebrande bestanden van je schijf.")
    result["burned"] = True
    emit(result)
    return result


def menu_font():
    if STYLE == "scrapbook":
        for cand in (
            "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        ):
            if Path(cand).is_file():
                return cand
    return font_path()


def menu_geometry(canvas):
    cw, ch = canvas
    if ch >= 560:
        return {
            "item_y": 126, "row_h": 46, "footer_y": ch - 70, "header_h": 84,
            "ts": 44, "tr": 25, "tf": 20, "tsub": 18, "bx": 52, "bw": cw - 104,
        }
    return {
        "item_y": 96, "row_h": 38, "footer_y": ch - 56, "header_h": 66,
        "ts": 34, "tr": 20, "tf": 17, "tsub": 15, "bx": 40, "bw": cw - 80,
    }


def _text_layer(path, name, value):
    f = path.parent / f"{path.stem}_{name}.txt"
    f.write_text(value)
    return f


def menu_png(path, title, subtitle, rows, footer, font, canvas, tv):
    cw, ch = canvas
    g = menu_geometry(canvas)
    scrap = STYLE == "scrapbook"
    parts = []
    texts = []

    if scrap:
        parts.append(f"noise=alls=7:allf=t")
        parts += [
            f"drawbox=x=0:y=0:w={cw}:h={g['header_h']}:color=0xb95343@0.92:t=fill",
            f"drawbox=x=0:y={g['header_h']}:w={cw}:h=4:color=0xe0b44c:t=fill",
            f"drawbox=x=0:y={ch - 6}:w={cw}:h=6:color=0x6d7f63:t=fill",
            f"drawbox=x=22:y=8:w=54:h=15:color=0xf0d07a@0.85:t=fill",
            f"drawbox=x={cw - 76}:y=8:w=54:h=15:color=0xf0d07a@0.85:t=fill",
            f"drawbox=x=18:y={g['header_h'] - 12}:w={cw - 36}:h=3:color=0xf0d07a@0.7:t=fill",
        ]
        bg, title_color, sub_color, row_color, foot_color = (
            "0xf1e3c5", "0xfff8e7", "0xf7ddc6", "0x3b2b22", "0xfff8e7"
        )
    else:
        parts = [
            f"drawbox=x=0:y=0:w={cw}:h={g['header_h']}:color=0x1b2738:t=fill",
            f"drawbox=x=0:y={g['header_h']}:w={cw}:h=2:color=0x2f6f9f:t=fill",
            f"drawbox=x=0:y={ch - 6}:w={cw}:h=6:color=0x1b2738:t=fill",
        ]
        bg, title_color, sub_color, row_color, foot_color = (
            "0x121a24", "0xffffff", "0x8fb4d9", "0xdfe9f3", "0xbfd4e7"
        )

    tf = _text_layer(path, "title", title)
    tx = 64 if scrap else "(w-tw)/2"
    ty = 16 if scrap else 14
    texts.append(
        f"drawtext=expansion=none:fontfile={font}:textfile={tf}:fontsize={g['ts']}:fontcolor={title_color}:x={tx}:y={ty}"
    )
    if subtitle:
        sf = _text_layer(path, "sub", subtitle)
        sx = 66 if scrap else "(w-tw)/2"
        sy = g["header_h"] - 34 if scrap else ch - 26
        texts.append(
            f"drawtext=expansion=none:fontfile={font}:textfile={sf}:fontsize={g['tsub']}:fontcolor="
            f"{'0x5a4434' if scrap else sub_color}:x={sx}:y={sy}"
        )

    for n, row in enumerate(rows):
        y = g["item_y"] + n * g["row_h"]
        rf = _text_layer(path, f"r{n}", row)
        if scrap:
            parts += [
                f"drawbox=x={g['bx'] + 4}:y={y - 4}:w={g['bw']}:h=42:color=0x6b573c@0.18:t=fill",
                f"drawbox=x={g['bx']}:y={y - 8}:w={g['bw']}:h=42:color=0xfffaf0:t=fill",
                f"drawbox=x={g['bx']}:y={y - 8}:w={g['bw']}:h=42:color=0xd7bd91:t=2",
                f"drawbox=x={g['bx'] - 12}:y={y - 13}:w=54:h=15:color=0xf0d07a@0.9:t=fill",
                f"drawbox=x={g['bx'] + 6}:y={y + 1}:w=34:h=24:color=0xb95343:t=fill",
            ]
            texts.append(
                f"drawtext=expansion=none:fontfile={font}:text={n + 1}:fontsize=18:fontcolor=0xfff8e7:"
                f"x={g['bx'] + 15}:y={y + 4}"
            )
            rx = g["bx"] + 52
        else:
            parts.append(
                f"drawbox=x={g['bx']}:y={y - 6}:w={g['bw']}:h=42:color=0x2a3d52:t=2"
            )
            rx = g["bx"] + 22
        texts.append(
            f"drawtext=expansion=none:fontfile={font}:textfile={rf}:fontsize={g['tr']}:fontcolor={row_color}:x={rx}:y={y + 2}"
        )

    fw = min(280, (cw - 3 * 44) // max(1, len(footer)))
    for n, row in enumerate(footer):
        x0 = 44 + n * (fw + 20)
        rf = _text_layer(path, f"f{n}", row)
        if scrap:
            parts += [
                f"drawbox=x={x0 + 3}:y={g['footer_y'] - 5}:w={fw}:h=42:color=0x6b573c@0.18:t=fill",
                f"drawbox=x={x0}:y={g['footer_y'] - 9}:w={fw}:h=42:color=0xfffaf0:t=fill",
                f"drawbox=x={x0}:y={g['footer_y'] - 9}:w={fw}:h=42:color=0xb95343:t=2",
            ]
        else:
            parts.append(
                f"drawbox=x={x0}:y={g['footer_y'] - 6}:w={fw}:h=42:color=0x22313f:t=2"
            )
        texts.append(
            f"drawtext=expansion=none:fontfile={font}:textfile={rf}:fontsize={g['tf']}:fontcolor={foot_color if not scrap else '0x8e3b2f'}:"
            f"x={x0}+({fw}-tw)/2:y={g['footer_y'] + 2}"
        )

    source = f"color=c={bg}:s={cw}x{ch}"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
            "-frames:v", "1", "-vf", ",".join(parts + texts), str(path),
        ]
    )


def highlight_png(path, buttons, canvas, tv):
    cw, ch = canvas
    sx = tv["w"] / cw
    scrap = STYLE == "scrapbook"
    boxes = []
    for b in buttons:
        x0 = round(b["x0"] * sx / 2) * 2
        y0 = round(b["y0"] / 2) * 2
        x1 = round(b["x1"] * sx / 2) * 2
        y1 = round(b["y1"] / 2) * 2
        if scrap:
            boxes.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=0xe8b04b@0.75:t=fill")
            boxes.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=white:t=3")
        else:
            boxes.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=white:t=fill")
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={tv['w']}x{tv['h']}",
            "-frames:v",
            "1",
            "-vf",
            ",".join(boxes) if boxes else "null",
            str(path),
        ]
    )


def spu_xml(path, buttons, hl):
    lines = ["<subpictures><stream>", f'<spu start="00:00:00.00" end="00:00:00.00" highlight="{hl}" select="{hl}">']
    for n, b in enumerate(buttons, 1):
        lines.append(
            f'<button name="b{n}" x0="{b["hx0"]}" y0="{b["hy0"]}" x1="{b["hx1"]}" y1="{b["hy1"]}"/>'
        )
    lines += ["</spu>", "</stream></subpictures>"]
    Path(path).write_text("\n".join(lines) + "\n")


def build_menu_vob(work, menu, tv, font):
    png = work / f"{menu['key']}.png"
    menu_png(png, menu["title"], menu["subtitle"], [e["label"] for e in menu["rows"]], [e["label"] for e in menu["footer"]], font, tv["canvas"], tv)
    mpg = work / f"{menu['key']}.mpg"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "25",
            "-i",
            str(png),
            "-frames:v",
            "1",
            "-vf",
            f"scale={tv['w']}:{tv['h']}",
            "-target",
            f"{menu_tv_name(tv)}-dvd",
            "-aspect",
            "16:9",
            str(mpg),
        ]
    )
    hl = work / f"{menu['key']}_hl.png"
    highlight_png(hl, menu["buttons"], tv["canvas"], tv)
    xml = work / f"{menu['key']}_spu.xml"
    spu_xml(xml, menu["buttons"], hl)
    out = work / f"{menu['key']}_spu.mpg"
    with open(mpg, "rb") as fin, open(out, "wb") as fout:
        r = subprocess.run(["spumux", str(xml)], stdin=fin, stdout=fout, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        die(f"spumux faalde: {r.stderr.strip()}")
    return out


def menu_tv_name(tv):
    return "pal" if tv["h"] == 576 else "ntsc"


def build_news_slide(work, item, number, tv, font):
    cw, ch = tv["canvas"]
    prefix = work / f"news{number:02d}"
    headline = prefix.with_suffix(".head.txt")
    lead = prefix.with_suffix(".lead.txt")
    source = prefix.with_suffix(".source.txt")
    headline.write_text("\n".join(wrap_lines(item["title"], 42, 3)))
    lead.write_text("\n".join(wrap_lines(item.get("lead") or "", 60, 4)))
    source.write_text(f"{item.get('date') or ''}\n{item['url']}")
    scrap = STYLE == "scrapbook"
    bg = "0xf2e4c4" if scrap else "0x121a24"
    ink = "0x4a3022" if scrap else "0xe8eef6"
    accent = "0xb95343" if scrap else "0x2f6f9f"
    filters = [
        f"drawbox=x=0:y=0:w={cw}:h=66:color={accent}:t=fill",
        f"drawtext=expansion=none:fontfile={font}:text='NIEUWS':fontsize=30:fontcolor={'0xfff8e7' if scrap else '0xffffff'}:x=48:y=16",
        f"drawtext=expansion=none:fontfile={font}:textfile={headline}:fontsize=30:fontcolor={ink}:x=64:y=110:line_spacing=10",
        f"drawtext=expansion=none:fontfile={font}:textfile={lead}:fontsize=22:fontcolor={ink}:x=64:y=250:line_spacing=9",
        f"drawtext=expansion=none:fontfile={font}:textfile={source}:fontsize=17:fontcolor={accent}:x=64:y={ch - 86}:line_spacing=8",
    ]
    out = work / f"news{number:02d}.mpg"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={bg}:s={cw}x{ch}:r=25",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            str(item.get("duration") or 8),
            "-vf",
            ",".join(filters),
            "-target",
            f"{menu_tv_name(tv)}-dvd",
            "-aspect",
            "16:9",
            "-c:a",
            "ac3",
            "-b:a",
            "192k",
            str(out),
        ]
    )
    return out


def build_text_slide(work, item, number, tv, font):
    cw, ch = tv["canvas"]
    prefix = work / f"text{number:02d}"
    headline = prefix.with_suffix(".head.txt")
    body = prefix.with_suffix(".body.txt")
    source = prefix.with_suffix(".source.txt")
    headline.write_text("\n".join(wrap_lines(item["title"], 44, 2)))
    body.write_text("\n".join(wrap_lines(item.get("body") or "", 62, 9)))
    source.write_text(str(item.get("source") or "Handmatig"))
    scrap = STYLE == "scrapbook"
    bg = "0xf2e4c4" if scrap else "0x121a24"
    ink = "0x4a3022" if scrap else "0xe8eef6"
    accent = "0xb95343" if scrap else "0x2f6f9f"
    filters = [
        f"drawbox=x=0:y=0:w={cw}:h=66:color={accent}:t=fill",
        f"drawtext=expansion=none:fontfile={font}:text='TEKST':fontsize=30:fontcolor=0xfff8e7:x=48:y=16",
        f"drawtext=expansion=none:fontfile={font}:textfile={headline}:fontsize=34:fontcolor={ink}:x=64:y=104:line_spacing=10",
        f"drawtext=expansion=none:fontfile={font}:textfile={body}:fontsize=22:fontcolor={ink}:x=64:y=196:line_spacing=9",
        f"drawtext=expansion=none:fontfile={font}:textfile={source}:fontsize=17:fontcolor={accent}:x=64:y={ch - 70}",
    ]
    out = work / f"text{number:02d}.mpg"
    run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={bg}:s={cw}x{ch}:r=25",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", str(item.get("duration") or 8),
            "-vf", ",".join(filters),
            "-target", f"{menu_tv_name(tv)}-dvd", "-aspect", "16:9",
            "-c:a", "ac3", "-b:a", "192k", str(out),
        ]
    )
    return out


def dvd_authoring(lib, items, work, iso, label, args):
    tv = TV[getattr(args, "tv", "pal")]
    font = menu_font()
    w, h = tv["w"], tv["h"]

    order = []
    for cat in lib.categories():
        rows = [i for i in items if i["category"] == cat]
        if rows:
            order.append((cat, rows))

    titles = []
    for cat, rows in order:
        for i in rows:
            titles.append(i)
    if len(titles) > 99:
        die("maximaal 99 titels per dvd")

    root_pages = [order[k : k + MENU_ROWS] for k in range(0, len(order), MENU_ROWS)] or [[]]
    cat_pages = {}
    for cat, rows in order:
        cat_pages[cat] = [rows[k : k + MENU_ROWS] for k in range(0, len(rows), MENU_ROWS)]

    def title_number(item):
        return titles.index(item) + 1

    menus = []
    vtsm_index = {}
    counter = 1
    for cat, _ in order:
        for page_no in range(len(cat_pages[cat])):
            vtsm_index[(cat, page_no)] = counter
            counter += 1

    for p, page in enumerate(root_pages):
        rows = [
            {"label": f"{cat}  ({len(items)})", "cmd": f"jump title {title_number(items[0])};"}
            for cat, items in page
        ]
        footer = []
        if p > 0:
            footer.append({"label": "< Vorige", "cmd": f"jump vmgm menu {p};"})
        if p < len(root_pages) - 1:
            footer.append({"label": "Volgende >", "cmd": f"jump vmgm menu {p + 2};"})
        menus.append(
            {
                "key": f"root{p + 1}",
                "zone": "vmgm",
                "title": "TUBE2DVD",
                "subtitle": f"categorieën {p + 1}/{len(root_pages)}" if len(root_pages) > 1 else "categorieën",
                "rows": rows,
                "footer": footer,
            }
        )

    for cat, _ in order:
        pages = cat_pages[cat]
        for p, page in enumerate(pages):
            rows = [
                {
                    "label": f"NIEUWS  {i['title'][:52]}" if i.get("kind") == "news"
                    else f"TEKST  {i['title'][:52]}" if i.get("kind") == "text"
                    else f"{human_dur(i['duration'])}  {i['title'][:52]}",
                    "cmd": f"jump title {title_number(i)};",
                }
                for i in page
            ]
            footer = [{"label": "< Terug", "cmd": "jump vmgm menu 1;"}]
            if p > 0:
                footer.append({"label": "< Vorige", "cmd": f"jump menu {vtsm_index[(cat, p - 1)]};"})
            if p < len(pages) - 1:
                footer.append({"label": "Volgende >", "cmd": f"jump menu {vtsm_index[(cat, p + 1)]};"})
            menus.append(
                {
                    "key": f"cat{vtsm_index[(cat, p)]}-{cat}",
                    "zone": "vtsm",
                    "title": cat,
                    "subtitle": f"{sum(len(x) for x in pages)} "
                    + ("berichten" if cat == NEWS_CATEGORY else "teksten" if cat == TEXT_CATEGORY else "video's")
                    + (f" · pagina {p + 1}/{len(pages)}" if len(pages) > 1 else ""),
                    "rows": rows,
                    "footer": footer,
                }
            )

    geo = menu_geometry(tv["canvas"])
    for menu in menus:
        menu["key"] = re.sub(r"[^A-Za-z0-9_.-]", "_", menu["key"])
        buttons = []
        for n in range(len(menu["rows"])):
            y = geo["item_y"] + n * geo["row_h"]
            buttons.append({"x0": geo["bx"], "x1": geo["bx"] + geo["bw"], "y0": y - 8, "y1": y + 34})
        fw = min(280, (tv["canvas"][0] - 3 * 44) // max(1, len(menu["footer"])))
        for n in range(len(menu["footer"])):
            x0 = 44 + n * (fw + 20)
            buttons.append({"x0": x0, "x1": x0 + fw, "y0": geo["footer_y"] - 9, "y1": geo["footer_y"] + 33})
        sx = w / tv["canvas"][0]
        for b in buttons:
            b["hx0"] = round(b["x0"] * sx / 2) * 2
            b["hx1"] = round(b["x1"] * sx / 2) * 2
            b["hy0"] = round(b["y0"] / 2) * 2
            b["hy1"] = round(b["y1"] / 2) * 2
        menu["buttons"] = buttons
        menu["vob"] = build_menu_vob(work, menu, tv, font)

    total_dur = sum(max(i.get("duration") or 0, 1) for i in titles)
    audio_bps = 192_000
    usable = DISC_BYTES * 0.96
    video_bps = int(usable * 8 / total_dur - audio_bps) if total_dur else 2_500_000
    video_bps = max(1_200_000, min(video_bps, 7_500_000))
    log(f"Video's: {len(titles)} · speelduur {human_dur(total_dur)} · {video_bps // 1000} kbps")

    mpegs = []
    for n, item in enumerate(titles, 1):
        if item.get("kind") == "news":
            log(f"  [{n}/{len(titles)}] nieuws: {item['title'][:60]}")
            mpegs.append(build_news_slide(work, item, n, tv, font))
            progress("Omzetten", n, len(titles), bytes_mode=False)
            continue
        if item.get("kind") == "text":
            log(f"  [{n}/{len(titles)}] tekst: {item['title'][:60]}")
            mpegs.append(build_text_slide(work, item, n, tv, font))
            progress("Omzetten", n, len(titles), bytes_mode=False)
            continue
        src = lib.root / item["file"]
        info = probe(src)
        ar = (info["width"] / info["height"]) if info["height"] else 16 / 9
        if abs(ar - 16 / 9) < 0.08:
            vf = f"scale={w}:{h},setsar={tv['sar']}"
        else:
            vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar={tv['sar']}"
            )
        out = work / f"t{n:02d}.mpg"
        if out.exists() and out.stat().st_size > 0:
            log(f"  [{n}/{len(titles)}] hergebruik: {item['title'][:60]}")
            mpegs.append(out)
            progress("Omzetten", n, len(titles), bytes_mode=False)
            continue
        log(f"  [{n}/{len(titles)}] {item['title'][:60]}")
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-vf",
                vf,
                "-target",
                f"{menu_tv_name(tv)}-dvd",
                "-b:v",
                str(video_bps),
                "-maxrate",
                str(min(int(video_bps * 1.6), 9_000_000)),
                "-bufsize",
                "1835k",
                "-aspect",
                "16:9",
                "-c:a",
                "ac3",
                "-b:a",
                "192k",
                "-ac",
                "2",
                str(out),
            ]
        )
        mpegs.append(out)

    dest = str((work / "dvdroot").resolve())
    lines = [
        f'<dvdauthor dest="{dest}" jumppad="0">',
        "  <vmgm>",
        "    <menus>",
    ]
    for menu in [m for m in menus if m["zone"] == "vmgm"]:
        lines += ["      <pgc>", f'        <vob file="{menu["vob"].resolve()}"/>']
        for n, e in enumerate(menu["rows"] + menu["footer"], 1):
            lines.append(f'        <button name="b{n}">{e["cmd"]}</button>')
        lines += ["      </pgc>"]
    lines += ["    </menus>", "  </vmgm>", "  <titleset>", "    <menus>"]
    for menu in [m for m in menus if m["zone"] == "vtsm"]:
        lines += ["      <pgc>", f'        <vob file="{menu["vob"].resolve()}"/>']
        for n, e in enumerate(menu["rows"] + menu["footer"], 1):
            lines.append(f'        <button name="b{n}">{e["cmd"]}</button>')
        lines += ["      </pgc>"]
    lines += ["    </menus>", "    <titles>"]
    for n, t in enumerate(titles):
        lines += [
            "      <pgc>",
            f'        <vob file="{mpegs[n].resolve()}"/>',
            "        <post>call menu;</post>",
            "      </pgc>",
        ]
    lines += ["    </titles>", "  </titleset>", "</dvdauthor>"]

    xml = work / "dvd.xml"
    xml.write_text("\n".join(lines) + "\n")
    shutil.rmtree(work / "dvdroot", ignore_errors=True)
    (work / "dvdroot").mkdir(parents=True)
    run(["dvdauthor", "-x", str(xml)])
    make_iso(work / "dvdroot", iso, label, video=True)


def build_dvd_video(lib, items, work, iso, label, args):
    dvd_authoring(lib, items, work, iso, label, args)


def cmd_burn(lib, args):
    burn(lib, args)


ISO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.iso$")


def iso_label(text, fallback):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "").strip()).strip("._-")
    return name[:32] or fallback


def dvd_categories(lib):
    cats = []
    for item in lib.items:
        cat = str(item.get("category") or "").strip()
        if cat and cat not in cats:
            cats.append(cat)
    return cats


def cat_select(name, current, lib):
    cats = dvd_categories(lib)
    if current and current in cats:
        cats.remove(current)
        cats.insert(0, current)
    opts = "".join(
        f'<option value="{escape(c)}"{" selected" if c == current else ""}>{escape(c)}</option>'
        for c in cats
    )
    opts += '<option value="__new__">+ nieuwe categorie</option>'
    return (
        f'<select name="{name}" onchange="var n=this.nextElementSibling;'
        "n.style.display=this.value==='__new__'?'block':'none';"
        "if(n.style.display==='block')n.focus()\">"
        + opts + "</select>"
        '<input name="new_category" placeholder="Naam nieuwe categorie" '
        'style="display:none;margin-top:4px">'
    )


def resolve_category(data, lib, default):
    cat = str(data.get("category") or "").strip()
    if cat == "__new__":
        cat = str(data.get("new_category") or "").strip()
    cat = cat or default
    lib.add_category(cat)
    return cat


def dvd_name(lib):
    return str(lib.db.get("dvd_name") or "")


def iso_files(root):
    build = root / "build"
    if not build.is_dir():
        return []
    return sorted(
        (p for p in build.glob("*.iso") if ISO_NAME_RE.match(p.name)),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )


def status_path(root):
    return root / "build" / "burn-status.json"


def read_burn_status(root):
    try:
        return json.loads(status_path(root).read_text())
    except (OSError, json.JSONDecodeError):
        return {"state": "idle", "text": ""}


def write_burn_status(root, state, text, pct=None, iso=""):
    data = {"state": state, "text": text, "iso": iso}
    if pct is not None:
        data["pct"] = pct
    status_path(root).write_text(json.dumps(data, ensure_ascii=False) + "\n")


def extract_preview(root, iso):
    dest = root / "build" / "preview" / iso.stem
    dest.mkdir(parents=True, exist_ok=True)
    index = dest / "index.html"
    def read_iso(name):
        return subprocess.run(
            ["isoinfo", "-J", "-i", str(iso), "-x", name], capture_output=True
        ).stdout

    if not index.exists() or index.stat().st_mtime < iso.stat().st_mtime:
        out = read_iso("/index.html")
        if not out:
            out = read_iso("/INDEX.HTML;1")
        index.write_bytes(out)
    thumbs = dest / "thumbs"
    if not thumbs.is_dir():
        thumbs.mkdir(parents=True)
        listing = subprocess.run(
            ["isoinfo", "-J", "-i", str(iso), "-f"], text=True, capture_output=True
        ).stdout
        for name in listing.splitlines():
            if name.lower().startswith("/thumbs/") and name.lower().endswith(
                (".jpg", ".png", ".webp")
            ):
                out = read_iso(name)
                if out:
                    target = dest / name.lstrip("/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(out)
    return dest


def burn_worker(root, iso):
    def status(state, text, pct=None):
        write_burn_status(root, state, text, pct, iso.name)

    try:
        status("busy", "Schijf voorbereiden")
        mounted = any(
            line.split()[0] == "/dev/sr0"
            for line in Path("/proc/mounts").read_text().splitlines() if line
        )
        if mounted:
            subprocess.run(["umount", "-l", "/dev/sr0"], capture_output=True)
        info = subprocess.run(
            ["dvd+rw-mediainfo", "/dev/sr0"], text=True, capture_output=True
        ).stdout
        if "Disc status:" in info and "blank" not in info:
            status("busy", "DVD+RW formatteren (duurt even)")
            r = subprocess.run(
                ["dvd+rw-format", "-force", "/dev/sr0"], text=True, capture_output=True
            )
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
        status("busy", "Branden")
        proc = subprocess.Popen(
            ["growisofs", "-dvd-compat", "-Z", f"/dev/sr0={iso}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in proc.stdout:
            m = re.search(r"(\d+\.\d+)% done", line)
            if m:
                status("busy", "Branden", float(m.group(1)))
        if proc.wait() != 0:
            raise RuntimeError("growisofs mislukt")
        lib = Library(root)
        label = iso.stem
        if not any(d.get("label") == label for d in lib.discs):
            lib.discs.append({
                "label": label, "mode": "data",
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "items": [i["id"] for i in lib.items], "bytes": iso.stat().st_size,
            })
            lib.save_discs()
        status("done", f"Gebrand: {iso.name}", 100)
        notify("tube2dvd", f"DVD gebrand: {iso.name}")
    except Exception as exc:
        status("error", str(exc)[:300])


WEB_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;color:#3b2b22;font:15px/1.5 "DejaVu Serif",Georgia,serif;
 background-color:#efe2c6;background-image:radial-gradient(#cdbb96 1px,transparent 1px),linear-gradient(115deg,#f6ecd6,#e8d8b6);
 background-size:22px 22px,100% 100%}
header{padding:26px 24px 6px}
h1{margin:0;font-size:32px;color:#8e3b2f;transform:rotate(-1deg);transform-origin:left center}
header p{margin:6px 0 0;color:#6d5842}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px}
.card{position:relative;background:#fffaf0;border:1px solid #d8c39a;border-radius:4px;
 box-shadow:3px 5px 0 rgba(90,66,38,.18);padding:18px 18px 20px;transform:rotate(-.5deg)}
.card:nth-child(even){transform:rotate(.6deg)}
.card:before{content:"";position:absolute;top:-11px;left:40%;width:66px;height:22px;background:#f0d98a;
 border-left:1px solid #d5b95c;border-right:1px solid #d5b95c;opacity:.9;transform:rotate(-3deg)}
h2{margin:0 0 14px;font-size:20px;color:#8e3b2f;border-bottom:2px solid #e0b44c;display:inline-block}
label{display:block;margin:10px 0 4px;font-size:13px;color:#6d5842;font-variant:small-caps}
input,select,textarea{width:100%;padding:9px 10px;border:1px solid #d7bd91;border-radius:4px;background:#fffdf7;color:#3b2b22;font:inherit}
textarea{min-height:92px;resize:vertical}
button{margin-top:14px;padding:10px 18px;border:0;border-radius:14px 5px 14px 5px;background:#c75d4a;color:#fff8e7;
 font:inherit;font-size:15px;cursor:pointer;box-shadow:3px 3px 0 #9c4032}
button:hover{background:#8e3b2f}
button.alt{background:#6d7f63;box-shadow:3px 3px 0 #4f5e48}
a{color:#8e3b2f}
.msg{background:#fff3d6;border:2px dashed #c75d4a;padding:12px 16px;margin:14px 0;border-radius:6px}
ul{list-style:none;padding:0}
li{background:#fffaf0;border:1px solid #d8c39a;margin:6px 0;padding:9px 12px;border-radius:3px;display:flex;gap:10px;align-items:center;min-width:0;overflow-wrap:anywhere}
li .tag{font-size:11px;padding:1px 7px;border-radius:8px 3px 8px 3px;background:#c75d4a;color:#fff8e7}
li .cat{font-size:12px;color:#8a7558;margin-left:auto}
li.done{opacity:.55}
.news-browser-bar{margin:14px 0 4px}
.news-browser-bar summary{cursor:pointer;font-size:13px;font-variant:small-caps;color:#8e3b2f}
.news-browser-bar iframe{width:100%;height:460px;margin-top:8px;border:1px solid #d8c39a;background:#fffaf0;border-radius:4px}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:18px 0}
.progress{height:14px;background:#d8c39a;border-radius:7px;overflow:hidden;flex:1;min-width:200px}
.progress i{display:block;height:100%;background:#6d7f63}
@media(max-width:640px){
 .wrap{padding:0 12px 40px}
 header{padding:18px 12px 4px}
 h1{font-size:26px;transform:none}
 .grid{grid-template-columns:minmax(0,1fr);gap:16px}
 .card,.card:nth-child(even){transform:none}
 .bar{display:block}
 .bar input,.bar select,.bar button,.card>form button{display:block;width:100%;margin:8px 0}
 li{flex-wrap:wrap;min-width:0}
 li .cat{margin-left:0;flex-basis:100%}
 .progress{min-width:0;width:100%}
}
"""


def web_page(lib, message=""):
    cats = lib.categories()
    current_name = dvd_name(lib)
    used = lib.size()
    pct = min(100, used / max(1, DEFAULT_TARGET) * 100)
    msg = f'<p class="msg">{escape(message)}</p>' if message else ""
    cat_input = cat_select("category", TEXT_CATEGORY, lib)
    news_cat = cat_select("category", NEWS_CATEGORY, lib)
    text_cat = cat_select("category", TEXT_CATEGORY, lib)
    rows = ""
    for i in lib.items:
        label = i["title"][:70]
        done = " done" if i.get("disc") else ""
        kind = i.get("kind", "video")
        edit = ""
        if kind in ("news", "text"):
            edit = f'<a href="/edit?id={urllib.parse.quote(i["id"])}">bewerk</a> '
        rows += (
            f'<li class="{done}"><span class="tag">{escape(kind)}</span>'
            f'<span>{escape(label)}</span><span class="cat">{edit}{escape(i["category"])} · '
            f'{escape(i.get("disc") or "pending")}</span>'
            f'<form method="post" action="/remove" style="margin:0">'
            f'<input type="hidden" name="id" value="{escape(i["id"])}">'
            '<button type="submit" style="margin:0;padding:4px 10px;font-size:12px">x</button></form></li>'
        )
    iso_rows = ""
    for iso in iso_files(lib.root):
        url = "/iso/" + urllib.parse.quote(iso.name) + "/preview/"
        iso_rows += (
            f'<li><span class="tag">iso</span><span><a href="{url}">{escape(iso.name)}</a><br>'
            f'<small>{human(iso.stat().st_size)} · '
            f'{datetime.fromtimestamp(iso.stat().st_mtime):%Y-%m-%d %H:%M}</small></span>'
            '<span class="cat"><span style="display:flex;gap:6px">'
            '<form method="post" action="/burn" style="margin:0" '
            "onsubmit=\"return confirm('Deze ISO branden? De oude inhoud van de DVD+RW wordt gewist.')\">"
            f'<input type="hidden" name="iso" value="{escape(iso.name)}">'
            '<button type="submit" style="margin:0;padding:4px 10px;font-size:12px">Brand</button>'
            '</form><form method="post" action="/delete-iso" style="margin:0" '
            "onsubmit=\"return confirm('Weet je zeker dat je deze ISO wilt verwijderen? "
            "De ISO en het opgeslagen voorbeeld worden definitief gewist.')\">"
            f'<input type="hidden" name="iso" value="{escape(iso.name)}">'
            '<button type="submit" class="alt" style="margin:0;padding:4px 10px;font-size:12px">Verwijder</button>'
            "</form></span></span></li>"
        )
    st = read_burn_status(lib.root)
    refresh = '<meta http-equiv="refresh" content="5">' if st.get("state") == "busy" else ""
    pct_status = f' ({st["pct"]:.0f}%)' if st.get("pct") is not None else ""
    status_card = (
        '<div class="card"><h2>Brandstatus</h2>'
        f'<p>{escape(st.get("text") or "Geen brandactie")}{pct_status}</p></div>'
    )
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
{refresh}
<title>{escape(current_name or 'tube2dvd')} — plakboek</title><style>{WEB_CSS}</style>
<header><h1>{escape(current_name or 'tube2dvd')}</h1><p>Alles voor de volgende dvd op één plek.</p></header>
<div class="wrap">{msg}
<div class="bar"><strong>{len(lib.items)} items · {human(used)} / {human(DEFAULT_TARGET)}</strong>
<div class="progress"><i style="width:{pct:.1f}%"></i></div>
<a href="/index.html" target="_blank">Bekijk het scrapbook-menu</a></div>
<div class="grid">
<div class="card"><h2>YouTube-video</h2>
<form method="post" action="/video">
<label>YouTube-URL</label><input name="query" placeholder="https://www.youtube.com/watch?v=..." required>
<label>Categorie</label>{cat_input}
<button type="submit">Video toevoegen</button></form>
<p><small>Plak de link van één video. De app haalt titel en duur op en zet hem in je plakboek.</small></p></div>
<div class="card"><h2>Nieuws van vandaag</h2>
<form method="post" action="/news">
<label>Aantal berichten</label><input type="number" name="count" value="12" min="1" max="50" required>
<label>Categorie</label>{news_cat}
<button type="submit">Nieuws + volledige artikelen ophalen</button></form>
<details class="news-browser-bar"><summary>Nieuws per bron bekijken vóór toevoegen</summary>
<iframe src="/news-browser" loading="lazy" title="Nieuws per bron"></iframe></details>
<p><small>Uit de eerste drie bronnen in de nieuws-app: Tweakers, nu.nl en Scientias wetenschap. Volledige artikelen worden lokaal opgeslagen.</small></p></div>
<div class="card"><h2>Eigen tekst</h2>
<form method="post" action="/note" enctype="multipart/form-data">
<label>Titel (optioneel)</label><input name="title" placeholder="Bijv. Verjaardag">
<label>Categorie</label>{text_cat}
<label>Tekst</label><textarea name="body" required></textarea>
<label>Plaatje (optioneel)</label><input type="file" name="image" accept="image/*">
<button type="submit">Tekst toevoegen</button></form></div>
<div class="card"><h2>Video van mobiel/laptop</h2>
<form method="post" action="/upload" enctype="multipart/form-data">
<label>Videobestand</label><input type="file" name="file" accept="video/*" required>
<label>Titel (optioneel)</label><input name="title" placeholder="Naam van de video">
<label>Categorie</label>{cat_input}
<button type="submit">Video uploaden</button></form></div>
<div class="card" style="grid-column:1/-1"><h2>Volgende dvd</h2>
<form method="post" action="/set-name" style="margin:0 0 12px">
<label style="margin:0">Naam van de dvd</label>
<input name="name" value="{escape(current_name)}" placeholder="Bijv. TUBE2DVD_04" style="margin:4px 0 8px">
<button type="submit" class="alt" style="margin:0">Naam opslaan</button></form>
<form method="post" action="/dvd" class="bar" style="margin:0 0 12px">
<label style="margin:0">Aantal nieuwsberichten</label>
<input type="number" name="news" value="0" min="0" max="50" style="width:90px">
<button type="submit" formaction="/preview" class="alt" style="margin:0">Bekijk voorbeeld-menu</button>
<button type="submit" formaction="/dvd" style="margin:0">Maak data-DVD ISO (HTML-menu)</button></form>
<form method="post" action="/new-disc" style="margin:0 0 12px" onsubmit="return confirm('Huidige collectie als ISO bewaren en met een lege nieuwe dvd beginnen?')">
<label style="margin:0">Naam van de nieuwe dvd</label>
<input name="label" value="{escape(current_name)}" placeholder="Bijv. TUBE2DVD_04" style="margin:4px 0 10px">
<button type="submit" class="alt" style="margin:0">Nieuwe dvd beginnen</button></form>
<ul>{rows or "<li>Nog geen items.</li>"}</ul></div>
<div class="card" style="grid-column:1/-1"><h2>DVD-ISO's</h2>
<ul>{iso_rows or "<li>Nog geen ISO's.</li>"}</ul>
<p><small>Open een ISO om het HTML-menu te bekijken en te testen. Met 'Brand' schrijf je die ISO naar de DVD+RW.</small></p></div>
{status_card}
</div>
<p><small><a href="/?plain=1">plakboek</a> · <a href="/index.html">html-menu</a></small></p>
</div>"""


def edit_page(lib, item, message=""):
    kind = item.get("kind", "news")
    article = item.get("article") or {}
    if kind == "news":
        text = "\n".join(
            ("## " if b.get("t") in ("h", "h2", "h3") else "")
            + str(b.get("x") or "")
            for b in article.get("blocks", [])
        )
    else:
        text = str(item.get("body") or "")
    msg = f'<p class="msg">{escape(message)}</p>' if message else ""
    lead = ""
    if kind == "news":
        lead = (
            '<label>Samenvatting</label><textarea name="lead" style="min-height:60px">'
            + escape(str(item.get("lead") or "")) + "</textarea>"
        )
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
<title>tube2dvd — bewerken</title><style>{WEB_CSS}</style>
<div class="wrap"><header><h1>Artikel bewerken</h1><p>{escape(item.get("title", ""))}</p></header>
<div class="card"><form method="post" action="/save-edit">
<input type="hidden" name="id" value="{escape(item["id"])}">
<input type="hidden" name="kind" value="{escape(kind)}">
<label>Titel</label><input name="title" value="{escape(str(item.get("title") or ""))}" required>
{lead}
<label>Volledige tekst (regel met ## = kop)</label>
<textarea name="text" style="min-height:340px">{escape(text)}</textarea>
<label>Bron</label><input name="source" value="{escape(str(item.get("source") or ""))}">
<label>Categorie</label>{cat_select("category", str(item.get("category") or ""), lib)}
<button type="submit">Opslaan</button>
<a href="/" style="margin-left:12px;font-size:13px">Annuleren</a></form>{msg}</div></div>"""


def make_web_handler(lib, args):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *params):
            pass

        def reply(self, body, status=200, kind="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if kind.startswith("text/html"):
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def refresh(self):
            fresh = Library(lib.root)
            lib.db = fresh.db
            lib.discs = fresh.discs

        def log_request(self, code="-", size="-"):
            with (lib.root / "build" / "web-requests.log").open("a") as fh:
                fh.write(
                    f"{datetime.now():%H:%M:%S} {code} {self.path} "
                    f"range={self.headers.get('Range', '-')} "
                    f"ua={self.headers.get('User-Agent', '')[:80]}\n"
                )

        def serve_file(self, target, kind=None):
            if not target.is_file():
                self.reply("niet gevonden", 404)
                return
            size = target.stat().st_size
            start, end = 0, size - 1
            wanted = self.headers.get("Range", "")
            if wanted.startswith("bytes="):
                spec = wanted[6:].split(",")[0].split("-")
                try:
                    if spec[0]:
                        start = int(spec[0])
                        if len(spec) > 1 and spec[1]:
                            end = int(spec[1])
                    elif len(spec) > 1 and spec[1]:
                        start = max(0, size - int(spec[1]))
                except ValueError:
                    start, end = 0, size - 1
            length = max(0, end - start + 1)
            self.send_response(206 if wanted else 200)
            self.send_header(
                "Content-Type", kind or mimetypes.guess_type(target.name)[0]
                or "application/octet-stream"
            )
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if wanted:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def form(self):
            length = int(self.headers.get("Content-Length", 0))
            if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                raw = self.rfile.read(length)
                msg = BytesParser(policy=email_policy).parsebytes(
                    b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
                )
                out = {}
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if not name:
                        continue
                    if part.get_filename():
                        out[name] = (part.get_filename(), part.get_payload(decode=True))
                    else:
                        out[name] = part.get_content()
                return out
            raw = self.rfile.read(length).decode("utf-8", "replace")
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        def page(self, message=""):
            self.reply(web_page(lib, message))

        def do_GET(self):
            self.refresh()
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                target = lib.root / "index.html"
                if path == "/index.html" and target.exists():
                    self.reply(target.read_text())
                else:
                    self.page()
                return
            if path.startswith(("/videos/", "/thumbs/")) or path in (
                "/nieuws.json", "/teksten.json"
            ):
                rel = urllib.parse.unquote(path).lstrip("/")
                target = (lib.root / rel).resolve()
                if lib.root.resolve() not in target.parents:
                    self.reply("niet gevonden", 404)
                    return
                self.serve_file(target)
                return
            if path.startswith("/media/"):
                rel = urllib.parse.unquote(path[len("/media/"):])
                target = (lib.root / rel).resolve()
                if lib.root.resolve() not in target.parents:
                    self.reply("niet gevonden", 404)
                    return
                self.serve_file(target)
                return
            if path.startswith("/iso/"):
                parts = [urllib.parse.unquote(x) for x in path.strip("/").split("/")]
                if len(parts) < 2 or not ISO_NAME_RE.match(parts[1]):
                    self.reply("niet gevonden", 404)
                    return
                iso = lib.root / "build" / parts[1]
                if not iso.is_file():
                    self.reply("niet gevonden", 404)
                    return
                dest = extract_preview(lib.root, iso)
                sub = "/".join(parts[3:]) if len(parts) > 3 else ""
                if not sub or sub == "index.html":
                    files = iso_files(lib.root)
                    idx = next((n for n, p in enumerate(files) if p.name == iso.name), 0)
                    prev = (f'<a href="/iso/{urllib.parse.quote(files[idx - 1].name)}/preview/">vorige</a>'
                            if idx > 0 else "")
                    nxt = (f'<a href="/iso/{urllib.parse.quote(files[idx + 1].name)}/preview/">volgende</a>'
                           if idx + 1 < len(files) else "")
                    nav = (
                        '<div style="padding:10px 24px;background:#efe2c6;'
                        'border-bottom:1px solid #cdbb96;font:14px system-ui">'
                        f'ISO {idx + 1}/{len(files)}: {escape(iso.name)} · {prev} {nxt} · '
                        '<a href="/">terug naar plakboek</a></div>'
                    )
                    html = (dest / "index.html").read_text(errors="replace")
                    opened = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + nav, html, count=1)
                    self.reply(opened if opened != html else nav + html)
                    return
                target = (dest / sub).resolve()
                if not target.is_file() and sub.startswith("videos/"):
                    target = (lib.root / sub).resolve()
                if lib.root.resolve() not in target.parents or not target.is_file():
                    self.reply("niet gevonden", 404)
                    return
                self.serve_file(target)
                return
            if path == "/news-browser":
                self.reply(news_browser_page(lib))
                return
            if path == "/news-preview":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                url = (q.get("url") or [""])[0]
                if not url.startswith(("http://", "https://")):
                    self.reply("ongeldige URL", 400)
                    return
                self.reply(news_preview_page(lib, url))
                return
            if path == "/edit":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ident = (q.get("id") or [""])[0]
                item = next((x for x in lib.items if x["id"] == ident), None)
                if not item or item.get("kind") not in ("news", "text"):
                    self.reply("niet gevonden", 404)
                    return
                self.reply(edit_page(lib, item))
                return
            self.reply("niet gevonden", 404)

        def do_POST(self):
            self.refresh()
            path = urllib.parse.urlparse(self.path).path
            try:
                data = self.form()
                if path == "/video":
                    query = str(data.get("query", "")).strip()
                    category = resolve_category(data, lib, "Overig")
                    if not query:
                        die("geen URL")
                    if not re.match(r"^(https?://|www\.)", query, re.I):
                        die("geen geldige YouTube-URL (begint met https://)")
                    meta = fetch_meta(query)
                    item = save_meta(lib, meta, category, args)
                    message = f"Video toegevoegd: {item['title'][:60]}" if item else "Video zit al in de bibliotheek"
                elif path == "/news":
                    news_args = argparse.Namespace(
                        count=int(data.get("count", 10)),
                        source=0,
                        all_sources=True,
                        category=resolve_category(data, lib, NEWS_CATEGORY),
                        slidesec=8,
                        no_body=False,
                        refresh=False,
                    )
                    imported = cmd_news(lib, news_args)
                    message = f"{len(imported)} nieuwsberichten met volledig artikel toegevoegd"
                elif path == "/news-add-one":
                    url = str(data.get("url", "")).strip()
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("ongeldige artikel-URL")
                    news_args = argparse.Namespace(
                        count=1, source=0, all_sources=False, urls=[url],
                        category=resolve_category(data, lib, NEWS_CATEGORY),
                        slidesec=8, no_body=False, refresh=False,
                    )
                    imported = cmd_news(lib, news_args)
                    message = (
                        f"Toegevoegd: {imported[0]['title'][:60]}" if imported
                        else "Artikel zat al in de dvd"
                    )
                    self.reply(news_preview_page(lib, url, message))
                    return
                elif path == "/note":
                    note_args = argparse.Namespace(
                        text=[str(data.get("body", ""))],
                        title=str(data.get("title", "")),
                        category=resolve_category(data, lib, TEXT_CATEGORY),
                        source="Handmatig",
                        slidesec=8,
                        image=data.get("image"),
                    )
                    cmd_note(lib, note_args)
                    message = "Tekst toegevoegd"
                elif path == "/upload":
                    up = data.get("file")
                    if not up or not isinstance(up, tuple):
                        die("geen bestand")
                    filename, payload = up
                    category = resolve_category(data, lib, "Overig")
                    title = str(data.get("title", "")).strip() or Path(filename).stem.replace("_", " ")
                    lib.add_category(category)
                    cat_dir = lib.root / "videos" / safe_name(category)
                    cat_dir.mkdir(parents=True, exist_ok=True)
                    dst = cat_dir / f"{slug(title)}{Path(filename).suffix.lower()}"
                    n = 2
                    while dst.exists():
                        dst = cat_dir / f"{slug(title)} [{n}]{Path(filename).suffix.lower()}"
                        n += 1
                    dst.write_bytes(payload)
                    info = probe(dst)
                    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(dst)], capture=True)
                    duration = int(float(r.stdout.strip() or 0))
                    ident = "file-" + hashlib.sha1(str(dst.relative_to(lib.root)).encode()).hexdigest()[:12]
                    thumb = lib.root / "thumbs" / f"{ident}.jpg"
                    grab_frame(dst, thumb, duration)
                    lib.items.append({
                        "id": ident, "title": title, "uploader": "", "url": "",
                        "category": category, "file": str(dst.relative_to(lib.root)),
                        "thumb": str(thumb.relative_to(lib.root)) if thumb.is_file() else "",
                        "duration": duration, "size": dst.stat().st_size,
                        "codec": info.get("vcodec", ""), "height": info.get("height", 0),
                        "added": datetime.now().strftime("%Y-%m-%d"),
                    })
                    lib.save()
                    lib.index_html()
                    message = f"Video geüpload: {title[:60]} ({human(dst.stat().st_size)})"
                elif path == "/remove":
                    ident = str(data.get("id", ""))
                    before = len(lib.items)
                    target = next((i for i in lib.items if i["id"] == ident), None)
                    if target:
                        lib.items.remove(target)
                        p = lib.root / target["file"] if target.get("file") else None
                        if p and p.exists():
                            p.unlink(missing_ok=True)
                    lib.save()
                    lib.index_html()
                    message = "Item verwijderd" if len(lib.items) < before else "Item niet gevonden"
                elif path == "/preview":
                    lib.index_html()
                    self.reply(lib.root.joinpath("index.html").read_text())
                    return
                elif path == "/new-disc":
                    if not lib.items:
                        raise ValueError("bibliotheek is leeg")
                    default_label = f"TUBE2DVD_{len(iso_files(lib.root)) + 1:02d}"
                    label = iso_label(data.get("label") or dvd_name(lib), default_label)
                    image = lib.root / "build" / f"{label}.iso"
                    if image.exists():
                        raise ValueError(f"ISO {label}.iso bestaat al")
                    burn_args = argparse.Namespace(
                        mode="data", tv="pal", label=label, device=None, all=True,
                        image=str(image), dry_run=False, keep_image=True, yes=True,
                    )
                    result = burn(lib, burn_args, mode="data")
                    old_ids = [i["id"] for i in lib.items]
                    try:
                        if not any(d.get("label") == label for d in lib.discs):
                            lib.discs.append({
                                "label": label, "mode": "data",
                                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "items": old_ids, "bytes": result["bytes"],
                            })
                        lib.save_discs()
                    finally:
                        lib.db["items"] = []
                        lib.save()
                        lib.index_html()
                    message = (
                        f"Nieuwe dvd begonnen; oude collectie staat in "
                        f"{result['iso']} ({human(result['bytes'])})"
                    )
                elif path == "/burn":
                    name = str(data.get("iso", ""))
                    if not ISO_NAME_RE.match(name):
                        raise ValueError("ongeldige iso-naam")
                    iso = lib.root / "build" / name
                    if not iso.is_file():
                        raise ValueError("iso niet gevonden")
                    if read_burn_status(lib.root).get("state") == "busy":
                        raise ValueError("er loopt al een brandactie")
                    write_burn_status(lib.root, "busy", "Brandactie gestart", iso=name)
                    threading.Thread(target=burn_worker, args=(lib.root, iso), daemon=True).start()
                    message = f"Branden gestart: {name}"
                elif path == "/delete-iso":
                    name = str(data.get("iso", ""))
                    if not ISO_NAME_RE.match(name):
                        raise ValueError("ongeldige iso-naam")
                    st = read_burn_status(lib.root)
                    if st.get("state") == "busy" and st.get("iso") == name:
                        raise ValueError("deze ISO wordt op dit moment gebrand")
                    iso = lib.root / "build" / name
                    if not iso.is_file():
                        raise ValueError("iso niet gevonden")
                    iso.unlink()
                    preview = lib.root / "build" / "preview" / iso.stem
                    if preview.is_dir():
                        shutil.rmtree(preview)
                    label = iso.stem
                    lib.discs = [d for d in lib.discs if d.get("label") != label]
                    lib.save_discs()
                    message = f"ISO verwijderd: {name}"
                elif path == "/set-name":
                    name = re.sub(r'[<>"\\/|]+', "", str(data.get("name") or "").strip())[:64]
                    if name:
                        lib.db["dvd_name"] = name
                    else:
                        lib.db.pop("dvd_name", None)
                    lib.save()
                    lib.index_html()
                    message = f"DVD-naam opgeslagen: {name}" if name else "DVD-naam gewist"
                elif path == "/save-edit":
                    ident = str(data.get("id", ""))
                    item = next((x for x in lib.items if x["id"] == ident), None)
                    if not item:
                        raise ValueError("artikel niet gevonden")
                    item["title"] = str(data.get("title") or "").strip() or item["title"]
                    item["category"] = resolve_category(data, lib, item["category"])
                    item["source"] = str(data.get("source") or item["source"])
                    raw = str(data.get("text") or "")
                    if item.get("kind") == "news":
                        item["lead"] = str(data.get("lead") or "")
                        blocks = []
                        for line in raw.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("## "):
                                blocks.append({"t": "h3", "x": line[3:].strip()})
                            else:
                                blocks.append({"t": "p", "x": line})
                        article = item.setdefault("article", {})
                        article["blocks"] = blocks
                        article["title"] = item["title"]
                        article["source"] = item["source"]
                    else:
                        item["body"] = raw
                    lib.save()
                    lib.index_html()
                    message = f"Artikel opgeslagen: {item['title'][:60]}"
                elif path == "/dvd":
                    dvd_args = argparse.Namespace(
                        mode="data", tv="pal", label=dvd_name(lib) or None, device=None, all=True,
                        image=str(lib.root / "build" / "web-dvd.iso"),
                        dry_run=True, keep_image=True, yes=True,
                    )
                    result = burn(lib, dvd_args, mode="data")
                    message = f"ISO klaar: {result['iso']} ({human(result['bytes'])})"
                else:
                    self.reply("niet gevonden", 404)
                    return
                self.page(message)
            except Exception as exc:
                self.page(f"Fout: {exc}")

    return Handler


def cmd_serve(lib, args):
    require("yt-dlp", "ffmpeg", "ffprobe")
    host = args.host
    if getattr(args, "tailscale", False):
        r = subprocess.run(["tailscale", "ip", "-4"], text=True, capture_output=True)
        ip = r.stdout.strip().splitlines()
        if r.returncode != 0 or not ip:
            die("tailscale-ip niet beschikbaar")
        host = ip[0]
    handler = make_web_handler(lib, args)
    server = ThreadingHTTPServer((host, args.port), handler)
    log(f"Plakboek-app: http://{host}:{args.port}/  (Ctrl+C om te stoppen)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", str(text))[:3])


def cmd_update(lib, args):
    repo = os.environ.get("TUBE2DVD_REPO", UPDATE_REPO)
    headers = {"User-Agent": "tube2dvd"}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers),
            timeout=15,
        ) as r:
            latest_info = json.load(r)
    except Exception as exc:
        die(f"update-check mislukt: {exc}")
    latest = str(latest_info.get("tag_name") or "").lstrip("v")
    if not latest:
        latest = VERSION
    if version_tuple(latest) <= version_tuple(VERSION) or getattr(args, "check", False):
        emit({"ok": True, "updated": False, "version": VERSION, "latest": latest})
        return
    if not getattr(args, "yes", False):
        if ask(f"Update naar {latest}? [j/N]: ").lower() not in ("j", "ja", "y", "yes"):
            die("afgebroken")
    raw = f"https://raw.githubusercontent.com/{repo}/main/tube2dvd.py"
    with urllib.request.urlopen(urllib.request.Request(raw, headers=headers), timeout=30) as r:
        new = r.read()
    if len(new) < 10_000 or b"def cmd_update" not in new:
        die("gedownloade update is ongeldig")
    script = Path(__file__).resolve()
    shutil.copy2(script, script.with_suffix(".py.bak"))
    tmp = script.with_suffix(".py.new")
    tmp.write_bytes(new)
    tmp.chmod(script.stat().st_mode)
    os.replace(tmp, script)
    log(f"Bijgewerkt naar {latest} (backup: {script.with_suffix('.py.bak')})")
    emit({"ok": True, "updated": True, "version": VERSION, "latest": latest})


def cmd_doctor(lib, args):
    tools = ["yt-dlp", "ffmpeg", "ffprobe", "mkisofs", "dvdauthor", "spumux", "growisofs", "xorriso"]
    missing = []
    for tool in tools:
        found = which(tool)
        if not found:
            missing.append(tool)
        log(f"  {'ok ' if found else 'mis'}  {tool:<12} {found or ''}")
    log(f"  font           {font_path()}")
    dev = Path(args.device or device_path())
    log(f"  brander        {dev} {'aanwezig' if dev.exists() else 'niet gevonden'}")
    log(f"  bibliotheek    {lib.root}")
    if dev.exists():
        out = run(["blockdev", "--getsize64", str(dev)], capture=True, check=False).stdout.strip()
        if out.isdigit() and int(out):
            log(f"  schijf         {human(int(out))} in {dev}")
        else:
            log("  schijf         geen schijf gevonden")
    if missing:
        pkgs = sorted({pkg_for(t) for t in missing})
        log("\nInstalleer: sudo pacman -S " + " ".join(pkgs))
    emit({
        "ok": True,
        "tools": {t: (which(t) or "") for t in tools},
        "device": str(dev),
        "disc_bytes": int(out) if out.isdigit() else 0,
        "missing_packages": sorted({pkg_for(t) for t in missing}),
    })


def add_collect_args(p):
    p.add_argument("spec", nargs="+", help="'Categorie=zoekterm' (herhaalbaar)")
    p.add_argument("--target", default=f"{DEFAULT_TARGET}", help="stopsize, bv 4GB (default 4GB)")
    p.add_argument("-n", "--limit", type=int, default=25, help="zoekresultaten per categorie")
    p.add_argument("--max-duration", type=int, default=900, help="sla video's langer dan dit aantal seconden over (default 15 min)")
    p.add_argument("-q", "--quality", type=int, default=720, choices=[360, 480, 720, 1080])
    p.add_argument("--crf", type=int, default=23, help="x264-kwaliteit (lager = beter)")


def build_parser():
    p = argparse.ArgumentParser(prog="tube2dvd", description="YouTube-video's verzamelen en op dvd branden")
    p.add_argument("--version", action="version", version=f"tube2dvd {VERSION}")
    p.add_argument("-d", "--dir", default=os.environ.get("TUBE2DVD_DIR"), help="bibliotheekmap (default ~/Videos/tube2dvd)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="wanneer is de schijf vol genoeg (bytes)")
    p.add_argument("--json", action="store_true", help="machinaal leesbare uitvoer (voor agents)")
    p.add_argument("--style", default="scrapbook", choices=["scrapbook", "classic"], help="menu-stijl")
    sub = p.add_subparsers(dest="cmd", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("-d", "--dir", default=argparse.SUPPRESS)
    shared.add_argument("--budget", type=int, default=argparse.SUPPRESS)
    shared.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    shared.add_argument("--style", default=argparse.SUPPRESS, choices=["scrapbook", "classic"])

    serve = sub.add_parser("serve", parents=[shared], help="start de plakboek-app in de browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--tailscale", action="store_true", help="luister op het Tailscale-IP")

    note = sub.add_parser("note", parents=[shared], help="voeg een eigen tekst toe")
    note.add_argument("text", nargs="+", help="de tekst")
    note.add_argument("--title", help="titel (optioneel)")
    note.add_argument("-c", "--category", default=TEXT_CATEGORY)
    note.add_argument("--source", default="Handmatig")
    note.add_argument("--slidesec", type=int, default=8)
    note.add_argument("--image", help="plaatje boven de tekst (bestandspad)")

    imp = sub.add_parser("import", parents=[shared], help="importeer lokale videobestanden")
    imp.add_argument("files", nargs="+", help="pad naar videobestand(en)")
    imp.add_argument("-c", "--category", default="Overig")
    imp.add_argument("--title", help="titel voor één bestand")

    add = sub.add_parser("add", parents=[shared], help="zoek, download en bewaar een video")
    add.add_argument("query", nargs="+", help="zoekterm of URL")
    add.add_argument("-c", "--category", help="categorie (slaat de vraag over)")
    add.add_argument("-q", "--quality", type=int, default=720, choices=[360, 480, 720, 1080])
    add.add_argument("--crf", type=int, default=23)
    add.add_argument("-n", "--limit", type=int, default=8)
    add.add_argument("-y", "--yes", action="store_true", help="geen vragen")
    add.add_argument("--disc-mode", default="data", choices=["data", "video"])

    sub.add_parser("list", parents=[shared], help="toon de bibliotheek")
    sub.add_parser("status", parents=[shared], help="ruimte en categorieën")

    news = sub.add_parser("news", parents=[shared], help="haal nieuws op uit de tweakers-nieuws plugin")
    news.add_argument("--count", type=int, default=10, help="aantal berichten")
    news.add_argument("--source", type=int, default=0, help="bronnummer uit de plugin (0 = alles)")
    news.add_argument("--all-sources", action="store_true", help="nieuws uit de eerste 3 bronnen van de nieuws-app")
    news.add_argument("-c", "--category", default=NEWS_CATEGORY)
    news.add_argument("--slidesec", type=int, default=8, help="duur van een nieuwsdia op dvd")
    news.add_argument("--no-body", action="store_true", help="alleen koppen, geen volledige artikelen ophalen")
    news.add_argument("--refresh", action="store_true", help="artikelen van bestaande berichten opnieuw ophalen")

    collect = sub.add_parser("collect", parents=[shared], help="download categorieën tot de doelgrootte")
    add_collect_args(collect)

    dvd = sub.add_parser("dvd", parents=[shared], help="verzamel video's + nieuws en maak een dvd")
    add_collect_args(dvd)
    dvd.add_argument("--news", type=int, default=10, help="aantal nieuwsberichten (0 = geen)")
    dvd.add_argument("--news-category", default=NEWS_CATEGORY)
    dvd.add_argument("--slidesec", type=int, default=8)
    dvd.add_argument("--no-body", action="store_true", help="geen volledige artikelen ophalen")
    dvd.add_argument("--tv", default="pal", choices=["pal", "ntsc"])
    dvd.add_argument("--label", help="volume-label")
    dvd.add_argument("--device", help="brander, bv /dev/sr0")
    dvd.add_argument("--burn", action="store_true", help="echt branden (anders alleen ISO)")
    dvd.add_argument("-y", "--yes", action="store_true", help="geen bevestiging")

    rm = sub.add_parser("remove", parents=[shared], help="verwijder een video uit de bibliotheek")
    rm.add_argument("what", nargs="?", help="titel of id")
    rm.add_argument("--burned", action="store_true", help="alle al gebrande video's")
    rm.add_argument("--keep", action="store_true", help="bestand op schijf laten")

    sub.add_parser("clean", parents=[shared], help="verwijder bestanden die al op een dvd staan")
    sub.add_parser("menu", parents=[shared], help="maak het html-menu")
    doc = sub.add_parser("doctor", parents=[shared], help="controleer alle benodigde programma's")
    doc.add_argument("--device", help="brander, bv /dev/sr0")

    burnp = sub.add_parser("burn", parents=[shared], help="brand een dvd")
    burnp.add_argument("--mode", default="data", choices=["data", "video"], help="datadisc of dvd-video")
    burnp.add_argument("--tv", default="pal", choices=["pal", "ntsc"])
    burnp.add_argument("--label", help="volume-label")
    burnp.add_argument("--device", help="brander, bv /dev/sr0")
    burnp.add_argument("--all", action="store_true", help="ook al eerder gebrande video's")
    burnp.add_argument("--image", help="alleen ISO maken op dit pad (niet branden)")
    burnp.add_argument("--dry-run", action="store_true", help="ISO maken zonder te branden")
    burnp.add_argument("--keep-image", action="store_true", help="ISO bewaren na branden")
    burnp.add_argument("-y", "--yes", action="store_true", help="geen bevestiging")

    upd = sub.add_parser("update", parents=[shared], help="werk tube2dvd bij vanaf GitHub")
    upd.add_argument("--check", action="store_true", help="alleen controleren of er een update is")
    upd.add_argument("-y", "--yes", action="store_true", help="geen bevestiging")
    return p


def main(argv=None):
    global JSON_MODE, STYLE
    args = build_parser().parse_args(argv)
    JSON_MODE = args.json
    STYLE = args.style
    root = args.dir or str(Path.home() / "Videos" / "tube2dvd")
    lib = Library(root)
    try:
        {
            "add": cmd_add,
            "serve": cmd_serve,
            "note": cmd_note,
            "import": cmd_import,
            "list": cmd_list,
            "status": cmd_status,
            "news": cmd_news,
            "collect": cmd_collect,
            "dvd": cmd_dvd,
            "remove": cmd_remove,
            "clean": cmd_clean,
            "menu": cmd_menu,
            "doctor": cmd_doctor,
            "burn": cmd_burn,
            "update": cmd_update,
        }[args.cmd](lib, args)
    except Fail as exc:
        if JSON_MODE:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        else:
            print(f"tube2dvd: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if JSON_MODE:
            print(json.dumps({"ok": False, "error": "afgebroken"}, ensure_ascii=False), flush=True)
        else:
            print("\nafgebroken", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
