"""Make a CapCut project for one clip by copying a finished project as a template.

Usage:
  python3 capcut_clip.py TEMPLATE NAME START LENGTH "hook text" [--video PATH]

START takes seconds, mm:ss or h:mm:ss. LENGTH is in seconds.
Run it with CapCut closed.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
BACKUPS = Path(__file__).parent / "backup"
US = 1_000_000
HOOK_SECONDS = 4


def seconds(text):
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def new_id():
    return str(uuid.uuid4()).upper()


def video_info(path):
    out = subprocess.run(
        ["mdls", "-raw", "-name", "kMDItemDurationSeconds", "-name", "kMDItemPixelWidth",
         "-name", "kMDItemPixelHeight", str(path)],
        capture_output=True, text=True).stdout.split("\0")
    if len(out) < 3 or "(null)" in out:
        sys.exit(f"Could not read length and size of {path}")
    # mdls prints the values in name order: duration, height, width
    return float(out[0]), int(out[2]), int(out[1])


def set_hook(material, text):
    content = json.loads(material["content"])
    content["text"] = text
    units = len(text.encode("utf-16-le")) // 2
    content["styles"] = content["styles"][:1]
    content["styles"][0]["range"] = [0, units]
    material["content"] = json.dumps(content, ensure_ascii=False)


def build_timeline(draft, timeline_id, start_us, length_us, hook, video):
    draft["id"] = timeline_id
    draft["duration"] = length_us

    kept = []
    dropped = set()
    for track in draft["tracks"]:
        segments = track["segments"]
        if track["type"] == "video":
            seg = segments[0]
            seg["source_timerange"] = {"start": start_us, "duration": length_us}
            seg["target_timerange"] = {"start": 0, "duration": length_us}
            track["segments"] = [seg]
            kept.append(track)
        elif track["type"] == "text" and track.get("flag") == 0:
            seg = segments[0]
            seg["target_timerange"]["start"] = 0
            seg["target_timerange"]["duration"] = min(HOOK_SECONDS * US, length_us)
            track["segments"] = [seg]
            hook_material = seg["material_id"]
            kept.append(track)
        else:
            # old auto captions belong to the template's audio, so they go
            for seg in segments:
                dropped.add(seg["material_id"])
                dropped.update(seg.get("extra_material_refs", []))
    draft["tracks"] = kept

    materials = draft["materials"]
    for template in materials["text_templates"]:
        if template["id"] in dropped:
            dropped.update(r["text_material_id"] for r in template.get("text_info_resources", [])
                           if "text_material_id" in r)
    materials["texts"] = [t for t in materials["texts"]
                          if t["id"] == hook_material or not t.get("recognize_task_id")]
    for kind in ("text_templates", "material_animations"):
        materials[kind] = [m for m in materials[kind] if m["id"] not in dropped]
    for text in materials["texts"]:
        if text["id"] == hook_material:
            set_hook(text, hook)

    if isinstance(draft.get("extra_info"), dict):
        draft["extra_info"]["subtitle_fragment_info_list"] = []

    if video:
        path, (length, width, height) = video
        clip = materials["videos"][0]
        clip["path"] = str(path)
        clip["material_name"] = path.name
        clip["duration"] = int(length * US)
        clip["width"], clip["height"] = width, height


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("template")
    parser.add_argument("name")
    parser.add_argument("start")
    parser.add_argument("length", type=float)
    parser.add_argument("hook")
    parser.add_argument("--video")
    args = parser.parse_args()

    if subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0:
        sys.exit("Close CapCut first.")

    source = ROOT / args.template
    target = ROOT / args.name
    if not source.is_dir():
        sys.exit(f"No project called {args.template}")
    if target.exists():
        sys.exit(f"A project called {args.name} already exists")

    video = None
    if args.video:
        path = Path(args.video).expanduser().resolve()
        video = (path, video_info(path))

    start_us = int(seconds(args.start) * US)
    length_us = int(args.length * US)
    now = int(time.time() * US)

    BACKUPS.mkdir(exist_ok=True)
    root_file = ROOT / "root_meta_info.json"
    shutil.copy2(root_file, BACKUPS / f"root_meta_info.{int(time.time())}.json")

    shutil.copytree(source, target)
    (target / ".locked").unlink(missing_ok=True)

    meta_file = target / "draft_meta_info.json"
    meta = json.loads(meta_file.read_text())
    old_draft_id = meta["draft_id"]
    old_timeline_id = json.loads((target / "draft_info.json").read_text())["id"]
    project_file = target / "Timelines/project.json"
    old_project_id = json.loads(project_file.read_text())["id"]
    draft_id, timeline_id, project_id = new_id(), new_id(), new_id()

    (target / "Timelines" / old_timeline_id).rename(target / "Timelines" / timeline_id)
    swaps = {old_draft_id: draft_id, old_timeline_id: timeline_id, old_project_id: project_id}
    for file in target.rglob("*"):
        if file.is_file() and file.suffix in (".json", ".bak", ".tmp"):
            text = file.read_text()
            changed = text
            for old, new in swaps.items():
                changed = changed.replace(old, new)
            if changed != text:
                file.write_text(changed)

    draft = json.loads((target / "draft_info.json").read_text())
    build_timeline(draft, timeline_id, start_us, length_us, args.hook, video)
    body = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
    for folder in (target, target / "Timelines" / timeline_id):
        for name in ("draft_info.json", "draft_info.json.bak", "template-2.tmp"):
            (folder / name).write_text(body)

    meta = json.loads(meta_file.read_text())
    meta["draft_name"] = args.name
    meta["draft_fold_path"] = str(target)
    meta["tm_duration"] = length_us
    meta["tm_draft_create"] = meta["tm_draft_modified"] = now
    if video:
        path, (length, width, height) = video
        for group in meta["draft_materials"]:
            for item in group["value"]:
                if item.get("metetype") == "video":
                    item["file_Path"] = str(path)
                    item["extra_info"] = path.name
                    item["duration"] = int(length * US)
                    item["roughcut_time_range"] = {"duration": int(length * US), "start": 0}
                    item["width"], item["height"] = width, height
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))

    root = json.loads(root_file.read_text())
    entry = dict(next(e for e in root["all_draft_store"] if e["draft_id"] == old_draft_id))
    entry.update(
        draft_id=draft_id,
        draft_name=args.name,
        draft_fold_path=str(target),
        draft_cover=str(target / "draft_cover.jpg"),
        draft_json_file=str(target / "draft_info.json"),
        tm_duration=length_us,
        tm_draft_create=now,
        tm_draft_modified=now,
    )
    root["all_draft_store"].insert(0, entry)
    root["draft_ids"] = root["draft_ids"] + 1
    root_file.write_text(json.dumps(root, ensure_ascii=False, separators=(",", ":")))

    print(f"Made project {args.name}: starts {args.start}, runs {args.length}s")


if __name__ == "__main__":
    main()
