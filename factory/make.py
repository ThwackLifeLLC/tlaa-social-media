#!/usr/bin/env python3
"""TLAA video factory: one run makes a batch of draft videos for Tim's weekly review.
Source article -> Claude script -> ElevenLabs voice + timing -> render -> quality check.
Nothing here posts anywhere.
Usage: make.py --count 3          (needs ANTHROPIC_API_KEY, ELEVENLABS_API_KEY)
       make.py --test             (no keys, offline checks of the timing code)
"""
import argparse, difflib, json, os, re, subprocess, sys, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
FEED = "https://thwacklife.com/blogs/arrows-and-insights.atom"
VOICE = "bIHbv24MWmeRgasZH58o"  # "Will", approved by Tim 2026-11-04
STATE = ROOT / "made.json"
COLORS = ["#FB5504", "#2EC4B6", "#F4C542", "#7BD389", "#FF6FA3", "#9B8CFF", "#58A6FF"]

RULES = """You write 30 to 45 second vertical videos for Thwack Life Archery Addiction, a culture-first community for U.S. target archers: recreational, competitive and adaptive.
Voice: a shooter talking to another shooter. Direct, warm, unpretentious, knowledgeable. No hype, no corporate filler, no exclamation stacking.
Hard rules:
- Use ONLY facts stated in the source article. Never add a number, name, date or claim that is not in it.
- Each video makes one specific, useful point from the article, and has its own hook. Never a generic overview.
- Target archery only. Never mention hunting or broadheads. Never say anti-elitism. Never lead with sales or discounts. Never imply expensive gear is required.
- Para and adaptive archers are competitive athletes, never inspirational objects.
- Never mention a mascot.
- The last sentence of the spoken script is exactly: Thwack life dot com.
Return ONLY JSON:
{"title": "...", "caption": "social caption, 2 to 4 short lines, ending with thwacklife.com and #ThwackLife #TargetArchery #Archery",
 "scenes": [{"say": "the exact words spoken in this scene", "type": "line|stack|card",
   "eyebrow": "2 to 5 words, optional", "head": "on-screen text, 8 words or fewer", "sub": "card subtext, 7 words or fewer",
   "items": ["for stack only: 2 or 3 items, 3 words or fewer each"]}]}
Use 5 to 8 scenes and at least two scene types. Finish with a scene whose say is exactly: Thwack life dot com."""


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": "tlaa-factory"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def articles():
    root = ET.fromstring(http(FEED))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        body = e.findtext("a:content", "", ns) or e.findtext("a:summary", "", ns)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
        out.append({"id": e.findtext("a:id", "", ns), "title": e.findtext("a:title", "", ns), "text": text,
                    "link": (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else "")})
    return out


def write_script(article):
    body = json.dumps({"model": "claude-sonnet-5-5", "max_tokens": 2000, "system": RULES,
                       "messages": [{"role": "user", "content": f"Source article: {article['title']}\n\n{article['text'][:12000]}"}]}).encode()
    raw = http("https://api.anthropic.com/v1/messages", body, {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
               "anthropic-version": "2023-06-01", "content-type": "application/json"})
    text = json.loads(raw)["content"][0]["text"]
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def words_from_alignment(al):
    """Turn ElevenLabs per-character timing into timed words."""
    words, cur, start, end = [], "", None, None
    for ch, s, e in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append({"text": cur, "start": round(start, 3), "end": round(end, 3)})
            cur, start = "", None
        else:
            if start is None:
                start = s
            cur += ch
            end = e
    if cur:
        words.append({"text": cur, "start": round(start, 3), "end": round(end, 3)})
    return words


def voice(script, mp3):
    body = json.dumps({"text": script, "model_id": "eleven_multilingual_v2"}).encode()
    raw = http(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps", body,
               {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "content-type": "application/json"})
    d = json.loads(raw)
    import base64
    mp3.write_bytes(base64.b64decode(d["audio_base64"]))
    return words_from_alignment(d["alignment"])


def transcribe(mp3):
    """Independent ear: what the voice file actually says."""
    bound = "tlaafactory"
    parts = (f'--{bound}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n'
             f'--{bound}\r\nContent-Disposition: form-data; name="file"; filename="v.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n').encode()
    body = parts + mp3.read_bytes() + f"\r\n--{bound}--\r\n".encode()
    raw = http("https://api.elevenlabs.io/v1/speech-to-text", body, {"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
               "content-type": f"multipart/form-data; boundary={bound}"})
    return json.loads(raw)["text"]


def similarity(a, b):
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def build_spec(vid, draft, words):
    """Pin each scene to the moment its words are spoken."""
    scenes, i = [], 0
    colors = iter(COLORS * 3)
    for n, s in enumerate(draft["scenes"]):
        count = len(s["say"].split())
        chunk = words[i:i + count]
        if len(chunk) != count:
            raise ValueError(f"scene {n}: words do not line up with the voice")
        start = 0 if n == 0 else chunk[0]["start"] - 0.12
        i += count
        scene = {"type": s.get("type", "line"), "start": round(start, 3), "eyebrow": s.get("eyebrow", "")}
        if n == len(draft["scenes"]) - 1:
            scene.update(type="end", line="No gatekeeping. No gear elitism.<br>Just real archery.", url="THWACKLIFE.COM")
        elif scene["type"] == "stack" and s.get("items"):
            step = (chunk[-1]["end"] - chunk[0]["start"]) / len(s["items"])
            scene.update(size=104, items=[{"t": t, "at": round(chunk[0]["start"] + k * step, 3), "a": k == len(s["items"]) - 1} for k, t in enumerate(s["items"])])
        elif scene["type"] == "card":
            scene.update(color=next(colors), head=s.get("head", ""), sub=s.get("sub", ""), size=124)
        else:
            scene.update(type="line", head=s.get("head", ""), sub=s.get("sub", ""), size=124)
        scenes.append(scene)
    if i != len(words):
        raise ValueError("script and voice have different word counts")
    duration = round(words[-1]["end"] + 1.4, 2)
    for a, b in zip(scenes, scenes[1:]):
        a["end"] = b["start"]
    scenes[-1]["end"] = duration + 0.1
    return {"id": vid, "title": draft["title"], "caption": draft["caption"], "logo": "assets/TLAA-Favicon.png",
            "audio": f"batch/{vid}.mp3", "duration": duration, "script": " ".join(s["say"] for s in draft["scenes"]),
            "scenes": scenes, "words": words}


def make_one(article, out):
    vid = re.sub(r"[^a-z0-9]+", "-", article["title"].lower()).strip("-")[:50]
    draft = write_script(article)
    script = " ".join(s["say"] for s in draft["scenes"])
    mp3 = out / f"{vid}.mp3"
    words = voice(script, mp3)
    heard = similarity(transcribe(mp3), script)
    if heard < 0.93:
        raise ValueError(f"voice does not match script ({heard:.2f})")
    spec = build_spec(vid, draft, words)
    spec["source"] = article["link"]
    spec_path = out / f"{vid}.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    video = out / f"{vid}.mp4"
    subprocess.run(["node", str(ROOT / "render.js"), str(spec_path), str(video)], check=True)
    subprocess.run([sys.executable, str(ROOT / "check.py"), str(video), str(spec_path)], check=True)
    return spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        al = {"characters": list("Hi there."), "character_start_times_seconds": [0, .1, .2, .3, .4, .5, .6, .7, .8],
              "character_end_times_seconds": [.1, .2, .3, .4, .5, .6, .7, .8, .9]}
        w = words_from_alignment(al)
        assert [x["text"] for x in w] == ["Hi", "there."] and w[1]["start"] == .3 and w[1]["end"] == .9
        draft = {"title": "t", "caption": "c", "scenes": [{"say": "Hi", "type": "line", "head": "HI"}, {"say": "there.", "head": "end"}]}
        s = build_spec("x", draft, w)
        assert s["scenes"][0]["end"] == s["scenes"][1]["start"] and s["scenes"][-1]["type"] == "end"
        assert similarity("Thwack life, dot com.", "thwack life dot com") == 1
        print("offline tests passed")
        return
    out = ROOT / "batch"
    out.mkdir(exist_ok=True)
    done = json.loads(STATE.read_text()) if STATE.exists() else []
    todo = [x for x in articles() if x["id"] not in done][:a.count]
    if not todo:
        print("no new articles")
    failed = 0
    for art in todo:
        try:
            spec = make_one(art, out)
            done.append(art["id"])
            print("made", spec["id"])
        except Exception as e:  # one bad video must not stop the batch; the run still ends red
            failed += 1
            print("FAILED", art["title"], e, file=sys.stderr)
    STATE.write_text(json.dumps(done, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
