"""Read caption word timing from a CapCut project, or re-cut the project to exact times.

Usage:
  python3 clip_adjust.py words NAME
  python3 clip_adjust.py trim NAME START END ["frame text"]

words lists every captioned word with its time in the source video.
trim sets the clip to START..END (source seconds, or mm:ss.s) and clears the
captions so they can be generated again. Frame text is optional; write \n
where the line should break. Run trim with CapCut closed.
"""
import json
import subprocess
import sys
from pathlib import Path

from capcut_clip import HOOK_SECONDS, ROOT, US, seconds, set_hook


def load(name):
    folder = ROOT / name
    return folder, json.loads((folder / "draft_info.json").read_text())


def video_segment(draft):
    return next(t for t in draft["tracks"] if t["type"] == "video")["segments"][0]


def stamp(sec):
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def words(name):
    _, draft = load(name)
    offset = video_segment(draft)["source_timerange"]["start"] / US
    texts = {t["id"]: t for t in draft["materials"]["texts"]}
    templates = {t["id"]: t for t in draft["materials"]["text_templates"]}
    rows = []
    for track in draft["tracks"]:
        if track["type"] != "text" or track.get("flag") == 0:
            continue
        for seg in track["segments"]:
            begin = seg["target_timerange"]["start"] / US
            material = templates.get(seg["material_id"])
            ids = ([r["text_material_id"] for r in material["text_info_resources"]]
                   if material else [seg["material_id"]])
            for text_id in ids:
                w = texts[text_id]["words"]
                for start, end, word in zip(w["start_time"], w["end_time"], w["text"]):
                    if word.strip():
                        rows.append((begin + start / 1000, begin + end / 1000, word.strip()))
    rows.sort()
    for start, end, word in rows:
        print(f"{stamp(offset + start)}  {stamp(offset + end)}  {word}")


def set_frame_text(draft, text):
    # two lines sit a little higher so the bottom line clears the video
    track = next(t for t in draft["tracks"] if t["type"] == "text" and t.get("flag") == 0)
    seg = track["segments"][0]
    material = next(t for t in draft["materials"]["texts"] if t["id"] == seg["material_id"])
    set_hook(material, text)
    seg["clip"]["transform"]["y"] = 0.43 if "\n" in text else 0.408


def trim(name, start, end, text=None):
    if subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0:
        sys.exit("Close CapCut first.")
    folder, draft = load(name)
    if text:
        set_frame_text(draft, text.replace("\\n", "\n"))
    start_us = int(seconds(start) * US)
    length_us = int(seconds(end) * US) - start_us

    seg = video_segment(draft)
    seg["source_timerange"] = {"start": start_us, "duration": length_us}
    seg["target_timerange"] = {"start": 0, "duration": length_us}
    draft["duration"] = length_us
    frame_text = next(t for t in draft["tracks"] if t["type"] == "text" and t.get("flag") == 0)
    frame_text["segments"][0]["target_timerange"] = {"start": 0, "duration": min(HOOK_SECONDS * US, length_us)}

    dropped = set()
    kept = []
    for track in draft["tracks"]:
        if track["type"] == "text" and track.get("flag") != 0:
            for s in track["segments"]:
                dropped.add(s["material_id"])
                dropped.update(s.get("extra_material_refs", []))
        else:
            kept.append(track)
    draft["tracks"] = kept
    materials = draft["materials"]
    for template in materials["text_templates"]:
        if template["id"] in dropped:
            dropped.update(r["text_material_id"] for r in template.get("text_info_resources", []))
    for kind in ("texts", "text_templates", "material_animations"):
        materials[kind] = [m for m in materials[kind] if m["id"] not in dropped]
    if isinstance(draft.get("extra_info"), dict):
        draft["extra_info"]["subtitle_fragment_info_list"] = []

    body = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
    for place in (folder, folder / "Timelines" / draft["id"]):
        for file in ("draft_info.json", "draft_info.json.bak", "template-2.tmp"):
            if (place / file).parent.is_dir():
                (place / file).write_text(body)

    meta_file = folder / "draft_meta_info.json"
    meta = json.loads(meta_file.read_text())
    meta["tm_duration"] = length_us
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))

    root_file = ROOT / "root_meta_info.json"
    root = json.loads(root_file.read_text())
    for entry in root["all_draft_store"]:
        if entry["draft_id"] == meta["draft_id"]:
            entry["tm_duration"] = length_us
    root_file.write_text(json.dumps(root, ensure_ascii=False, separators=(",", ":")))
    print(f"{name}: now {start} to {end}, {length_us / US:.2f}s, captions cleared")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "words":
        words(sys.argv[2])
    elif len(sys.argv) in (5, 6) and sys.argv[1] == "trim":
        trim(*sys.argv[2:])
    else:
        sys.exit(__doc__)
