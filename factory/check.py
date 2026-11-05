#!/usr/bin/env python3
"""Quality gate for a finished video. Exit 1 blocks it from going out.
Usage: check.py video.mp4 spec.json"""
import json, subprocess, sys, re
video, spec_path = sys.argv[1:3]
spec = json.load(open(spec_path))
probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", video]))
streams = probe["streams"]
v = next((s for s in streams if s["codec_type"] == "video"), None)
a = next((s for s in streams if s["codec_type"] == "audio"), None)
fails = []
def need(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)
need(v is not None, "has video")
need(a is not None, "has an audio track")
if v:
    need((int(v["width"]), int(v["height"])) == (1080, 1920), f"size 1080x1920 (got {v['width']}x{v['height']})")
    need(v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p", "h264 yuv420p")
dur = float(probe["format"]["duration"])
need(abs(dur - spec["duration"]) < 0.25, f"duration {dur:.2f}s matches plan {spec['duration']}s")
need(dur <= 60, "60 seconds or less")
need(float(probe["format"]["size"]) < 100e6, "under 100 MB")
if a:
    need(abs(float(a["duration"]) - dur) < 0.25, "audio runs the full length")
    log = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", log)
    need(m is not None and float(m.group(1)) > -40, f"audio is not silent (mean {m.group(1) if m else '?'} dB)")
# The spoken words must be the approved script.
norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()
need(norm(" ".join(w["text"] for w in spec["words"])) == norm(spec["script"]), "voice matches the script word for word")
# Brand rules.
text = json.dumps(spec).lower()
for bad in ["anti-elitis", "antielitis", "broadhead", "hunting", "discount", "% off", "range monkee"]:
    need(bad not in text, f"no banned wording: {bad}")
print("RESULT:", "BLOCKED" if fails else "OK")
sys.exit(1 if fails else 0)
