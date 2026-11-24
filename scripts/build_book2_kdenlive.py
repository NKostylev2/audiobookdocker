#!/usr/bin/env python3
"""
Generate a production-ready Kdenlive (MLT XML) project for Book 2 chapters:
- Top Track: 10s intro video with 1s qtblend fade-out (frames 270-300).
- BG Track: book2_video_ref.mp4 looped seamlessly from frame 270 to end.
- Audio Track: 10s intro audio + full chapter master WAV.
"""

import os
import sys
import json
import math
import uuid
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

def get_duration_info(path: str):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size",
        "-of", "json", path
    ]
    data = json.loads(subprocess.check_output(cmd).decode())
    dur_s = float(data["format"]["duration"])
    size_b = int(data["format"]["size"])
    return dur_s, size_b

def format_tc(seconds: float, fps: int = 30) -> str:
    total_ms = int(round(seconds * 1000))
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def generate_kdenlive_project(
    wav_path: str,
    output_kdenlive: str,
    intro_path: str = "/home/nk/Загрузки/Telegram Desktop/gemini_generated_video_61ea82f7.mp4",
    bg_video_path: str = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4",
    fps: int = 30
):
    wav_path = os.path.abspath(wav_path)
    output_kdenlive = os.path.abspath(output_kdenlive)
    intro_path = os.path.abspath(intro_path)
    bg_video_path = os.path.abspath(bg_video_path)

    audio_dur_s, audio_size = get_duration_info(wav_path)
    intro_dur_s, intro_size = get_duration_info(intro_path)
    bg_dur_s, bg_size = get_duration_info(bg_video_path)

    intro_frames = int(round(10.0 * fps)) # exactly 300 frames (0 to 299)
    audio_frames = int(round(audio_dur_s * fps))
    total_frames = intro_frames + audio_frames
    total_dur_s = 10.0 + audio_dur_s

    bg_loop_frames = int(round(bg_dur_s * fps)) # 17340 frames

    # Background video starts at frame 270 (9.0 seconds, start of fade transition)
    bg_start_frame = 270
    needed_bg_frames = total_frames - bg_start_frame

    num_full_bg_loops = needed_bg_frames // bg_loop_frames
    rem_bg_frames = needed_bg_frames % bg_loop_frames
    num_bg_entries = num_full_bg_loops + (1 if rem_bg_frames > 0 else 0)

    print(f"Audio Duration: {audio_dur_s:.2f}s ({audio_frames} frames)")
    print(f"Total Duration: {total_dur_s:.2f}s ({total_frames} frames)")
    print(f"BG Loops needed: {num_bg_entries} (full={num_full_bg_loops}, rem={rem_bg_frames})")

    # Build XML
    mlt = ET.Element("mlt", {
        "LC_NUMERIC": "C",
        "producer": "main_bin",
        "root": "/home/nk",
        "version": "7.9.0"
    })

    ET.SubElement(mlt, "profile", {
        "colorspace": "709",
        "description": "1920x1080 30fps",
        "display_aspect_den": "9",
        "display_aspect_num": "16",
        "frame_rate_den": "1",
        "frame_rate_num": "30",
        "height": "1080",
        "progressive": "1",
        "sample_aspect_den": "1",
        "sample_aspect_num": "1",
        "width": "1920"
    })

    # producer0: black background
    prod0 = ET.SubElement(mlt, "producer", {
        "id": "producer0",
        "in": "00:00:00.000",
        "out": "06:00:00.000"
    })
    for k, v in [
        ("length", "2147483647"), ("eof", "continue"), ("resource", "black"),
        ("aspect_ratio", "1"), ("mlt_service", "color"),
        ("kdenlive:playlistid", "black_track"), ("mlt_image_format", "rgba")
    ]:
        p = ET.SubElement(prod0, "property", {"name": k})
        p.text = v

    bg_uuid = str(uuid.uuid4())
    intro_uuid = str(uuid.uuid4())
    audio_uuid = str(uuid.uuid4())
    doc_uuid = f"{{{uuid.uuid4()}}}"

    # Bin chains
    # chain0: bg_video
    c0 = ET.SubElement(mlt, "chain", {"id": "chain0", "out": format_tc(bg_dur_s)})
    for k, v in [
        ("length", str(bg_loop_frames)), ("eof", "pause"), ("resource", bg_video_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "0"), ("audio_index", "1"), ("kdenlive:id", "2"),
        ("kdenlive:control_uuid", f"{{{bg_uuid}}}"), ("kdenlive:folderid", "-1"),
        ("kdenlive:file_size", str(bg_size))
    ]:
        p = ET.SubElement(c0, "property", {"name": k})
        p.text = v

    # chain1: intro_video
    c1 = ET.SubElement(mlt, "chain", {"id": "chain1", "out": "00:00:10.000"})
    for k, v in [
        ("length", "301"), ("eof", "pause"), ("resource", intro_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "0"), ("audio_index", "1"), ("kdenlive:id", "3"),
        ("kdenlive:control_uuid", f"{{{intro_uuid}}}"), ("kdenlive:folderid", "-1"),
        ("kdenlive:file_size", str(intro_size))
    ]:
        p = ET.SubElement(c1, "property", {"name": k})
        p.text = v

    # chain2: audio_wav
    c2 = ET.SubElement(mlt, "chain", {"id": "chain2", "out": format_tc(audio_dur_s)})
    for k, v in [
        ("length", str(audio_frames)), ("eof", "pause"), ("resource", wav_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "-1"), ("audio_index", "0"), ("kdenlive:id", "4"),
        ("kdenlive:control_uuid", f"{{{audio_uuid}}}"), ("kdenlive:folderid", "-1"),
        ("kdenlive:file_size", str(audio_size))
    ]:
        p = ET.SubElement(c2, "property", {"name": k})
        p.text = v

    # Background loop chains
    bg_chain_ids = []
    chain_idx = 3
    for i in range(num_bg_entries):
        cid = f"chain{chain_idx}"
        chain_idx += 1
        bg_chain_ids.append(cid)
        is_last = (i == num_bg_entries - 1)
        dur = (rem_bg_frames / fps) if (is_last and rem_bg_frames > 0) else bg_dur_s
        c = ET.SubElement(mlt, "chain", {"id": cid, "out": format_tc(dur)})
        for k, v in [
            ("length", str(bg_loop_frames)), ("eof", "pause"), ("resource", bg_video_path),
            ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
            ("video_index", "0"), ("audio_index", "1"),
            ("kdenlive:control_uuid", f"{{{bg_uuid}}}"), ("kdenlive:id", "2"),
            ("kdenlive:folderid", "-1"), ("kdenlive:file_size", str(bg_size))
        ]:
            p = ET.SubElement(c, "property", {"name": k})
            p.text = v

    # Top intro video chain (chain_idx)
    intro_video_cid = f"chain{chain_idx}"
    chain_idx += 1
    civ = ET.SubElement(mlt, "chain", {"id": intro_video_cid, "out": "00:00:10.000"})
    for k, v in [
        ("length", "301"), ("eof", "pause"), ("resource", intro_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "0"), ("audio_index", "1"),
        ("kdenlive:control_uuid", f"{{{intro_uuid}}}"), ("kdenlive:id", "3"),
        ("kdenlive:folderid", "-1"), ("kdenlive:file_size", str(intro_size))
    ]:
        p = ET.SubElement(civ, "property", {"name": k})
        p.text = v

    # Audio intro chain (chain_idx)
    intro_audio_cid = f"chain{chain_idx}"
    chain_idx += 1
    cia = ET.SubElement(mlt, "chain", {"id": intro_audio_cid, "out": "00:00:10.000"})
    for k, v in [
        ("length", "301"), ("eof", "pause"), ("resource", intro_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "0"), ("audio_index", "1"),
        ("kdenlive:control_uuid", f"{{{intro_uuid}}}"), ("kdenlive:id", "3"),
        ("kdenlive:folderid", "-1"), ("kdenlive:file_size", str(intro_size))
    ]:
        p = ET.SubElement(cia, "property", {"name": k})
        p.text = v

    # Audio chapter WAV chain (chain_idx)
    audio_wav_cid = f"chain{chain_idx}"
    caw = ET.SubElement(mlt, "chain", {"id": audio_wav_cid, "out": format_tc(audio_dur_s)})
    for k, v in [
        ("length", str(audio_frames)), ("eof", "pause"), ("resource", wav_path),
        ("mlt_service", "avformat-novalidate"), ("seekable", "1"),
        ("video_index", "-1"), ("audio_index", "0"),
        ("kdenlive:control_uuid", f"{{{audio_uuid}}}"), ("kdenlive:id", "4"),
        ("kdenlive:folderid", "-1"), ("kdenlive:file_size", str(audio_size))
    ]:
        p = ET.SubElement(caw, "property", {"name": k})
        p.text = v

    # main_bin playlist
    mb = ET.SubElement(mlt, "playlist", {"id": "main_bin"})
    ET.SubElement(mb, "entry", {"producer": "chain0", "in": "00:00:00.000", "out": format_tc(bg_dur_s)})
    ET.SubElement(mb, "entry", {"producer": "chain1", "in": "00:00:00.000", "out": "00:00:10.000"})
    ET.SubElement(mb, "entry", {"producer": "chain2", "in": "00:00:00.000", "out": format_tc(audio_dur_s)})

    # playlist0 (Audio Track 1)
    pl0 = ET.SubElement(mlt, "playlist", {"id": "playlist0"})
    e_ia = ET.SubElement(pl0, "entry", {"producer": intro_audio_cid, "in": "00:00:00.000", "out": "00:00:09.967"})
    p_id = ET.SubElement(e_ia, "property", {"name": "kdenlive:id"})
    p_id.text = "3"
    e_cw = ET.SubElement(pl0, "entry", {"producer": audio_wav_cid, "in": "00:00:00.000", "out": format_tc(audio_dur_s)})
    p_id2 = ET.SubElement(e_cw, "property", {"name": "kdenlive:id"})
    p_id2.text = "4"

    # playlist1 (Audio Track 1 empty second half)
    ET.SubElement(mlt, "playlist", {"id": "playlist1"})

    # tractor0 (Audio 1)
    tr0 = ET.SubElement(mlt, "tractor", {"id": "tractor0", "in": "00:00:00.000"})
    for k, v in [
        ("kdenlive:trackheight", "75"), ("kdenlive:timeline_active", "1"),
        ("kdenlive:collapsed", "0"), ("kdenlive:audio_rec", "")
    ]:
        p = ET.SubElement(tr0, "property", {"name": k})
        p.text = v
    ET.SubElement(tr0, "track", {"hide": "video", "producer": "playlist0"})
    ET.SubElement(tr0, "track", {"hide": "video", "producer": "playlist1"})

    # playlist2 & 3 (Audio Track 2 - unused)
    ET.SubElement(mlt, "playlist", {"id": "playlist2"})
    ET.SubElement(mlt, "playlist", {"id": "playlist3"})
    tr1 = ET.SubElement(mlt, "tractor", {"id": "tractor1", "in": "00:00:00.000"})
    for k, v in [
        ("kdenlive:trackheight", "75"), ("kdenlive:timeline_active", "1"),
        ("kdenlive:collapsed", "0"), ("kdenlive:audio_rec", "")
    ]:
        p = ET.SubElement(tr1, "property", {"name": k})
        p.text = v
    ET.SubElement(tr1, "track", {"hide": "video", "producer": "playlist2"})
    ET.SubElement(tr1, "track", {"hide": "video", "producer": "playlist3"})

    # playlist4 (Video Track 1 - Background Video Looped)
    pl4 = ET.SubElement(mlt, "playlist", {"id": "playlist4"})
    ET.SubElement(pl4, "blank", {"length": "270"})
    for i, cid in enumerate(bg_chain_ids):
        is_last = (i == num_bg_entries - 1)
        dur = (rem_bg_frames / fps) if (is_last and rem_bg_frames > 0) else bg_dur_s
        entry = ET.SubElement(pl4, "entry", {"producer": cid, "in": "00:00:00.000", "out": format_tc(dur)})
        p = ET.SubElement(entry, "property", {"name": "kdenlive:id"})
        p.text = "2"

    ET.SubElement(mlt, "playlist", {"id": "playlist5"})
    tr2 = ET.SubElement(mlt, "tractor", {"id": "tractor2", "in": "00:00:00.000"})
    for k, v in [
        ("kdenlive:trackheight", "75"), ("kdenlive:timeline_active", "1"),
        ("kdenlive:collapsed", "0"), ("kdenlive:audio_rec", "")
    ]:
        p = ET.SubElement(tr2, "property", {"name": k})
        p.text = v
    ET.SubElement(tr2, "track", {"producer": "playlist4"})
    ET.SubElement(tr2, "track", {"producer": "playlist5"})

    # playlist6 (Video Track 2 - Intro on Top with fade-out)
    pl6 = ET.SubElement(mlt, "playlist", {"id": "playlist6"})
    e_iv = ET.SubElement(pl6, "entry", {"producer": intro_video_cid, "in": "00:00:00.000", "out": "00:00:09.967"})
    p = ET.SubElement(e_iv, "property", {"name": "kdenlive:id"})
    p.text = "3"
    filt = ET.SubElement(e_iv, "filter", {"id": "filter7"})
    
    # Generate rect animation: 0-269 opacity=1, 270-300 opacity=1.0 -> 0.0
    rect_parts = []
    for f in range(270):
        rect_parts.append(f"{f}=0 0 1920 1080 1")
    for f in range(270, 301):
        progress = (f - 270) / 30.0
        opacity = 0.5 * (1.0 + math.cos(math.pi * progress))
        rect_parts.append(f"{f}=0 0 1920 1080 {opacity:.3f}")
    
    rot_parts = [f"{f}=0" for f in range(301)]

    p_rect = ET.SubElement(filt, "property", {"name": "rect"})
    p_rect.text = ";".join(rect_parts)
    p_rot = ET.SubElement(filt, "property", {"name": "rotation"})
    p_rot.text = ";".join(rot_parts)
    p_srv = ET.SubElement(filt, "property", {"name": "mlt_service"})
    p_srv.text = "qtblend"

    ET.SubElement(mlt, "playlist", {"id": "playlist7"})
    tr3 = ET.SubElement(mlt, "tractor", {"id": "tractor3", "in": "00:00:00.000"})
    for k, v in [
        ("kdenlive:trackheight", "75"), ("kdenlive:timeline_active", "1"),
        ("kdenlive:collapsed", "0"), ("kdenlive:audio_rec", "")
    ]:
        p = ET.SubElement(tr3, "property", {"name": k})
        p.text = v
    ET.SubElement(tr3, "track", {"producer": "playlist6"})
    ET.SubElement(tr3, "track", {"producer": "playlist7"})

    # Sequence Tractor
    seq_tr = ET.SubElement(mlt, "tractor", {
        "id": doc_uuid,
        "in": "00:00:00.000",
        "out": format_tc(total_dur_s)
    })
    for k, v in [
        ("kdenlive:uuid", doc_uuid),
        ("kdenlive:clipname", "Sequence 1"),
        ("kdenlive:sequenceproperties.hasAudio", "1"),
        ("kdenlive:sequenceproperties.hasVideo", "1"),
        ("kdenlive:sequenceproperties.tracksCount", "4"),
        ("kdenlive:sequenceproperties.documentuuid", doc_uuid),
        ("kdenlive:duration", format_tc(total_dur_s))
    ]:
        p = ET.SubElement(seq_tr, "property", {"name": k})
        p.text = v

    for prod in ["producer0", "tractor0", "tractor1", "tractor2", "tractor3"]:
        ET.SubElement(seq_tr, "track", {"producer": prod})

    # Transitions
    t0 = ET.SubElement(seq_tr, "transition", {"id": "transition0"})
    for k, v in [("a_track", "0"), ("b_track", "1"), ("mlt_service", "mix"),
                 ("kdenlive_id", "mix"), ("always_active", "1"), ("accepts_blanks", "1"), ("sum", "1")]:
        p = ET.SubElement(t0, "property", {"name": k})
        p.text = v

    t1 = ET.SubElement(seq_tr, "transition", {"id": "transition1"})
    for k, v in [("a_track", "0"), ("b_track", "2"), ("mlt_service", "mix"),
                 ("kdenlive_id", "mix"), ("always_active", "1"), ("accepts_blanks", "1"), ("sum", "1")]:
        p = ET.SubElement(t1, "property", {"name": k})
        p.text = v

    t2 = ET.SubElement(seq_tr, "transition", {"id": "transition2"})
    for k, v in [("a_track", "0"), ("b_track", "3"), ("compositing", "0"),
                 ("distort", "0"), ("rotate_center", "0"), ("mlt_service", "qtblend"),
                 ("kdenlive_id", "qtblend"), ("always_active", "1")]:
        p = ET.SubElement(t2, "property", {"name": k})
        p.text = v

    t3 = ET.SubElement(seq_tr, "transition", {"id": "transition3"})
    for k, v in [("a_track", "0"), ("b_track", "4"), ("compositing", "0"),
                 ("distort", "0"), ("rotate_center", "0"), ("mlt_service", "qtblend"),
                 ("kdenlive_id", "qtblend"), ("always_active", "1")]:
        p = ET.SubElement(t3, "property", {"name": k})
        p.text = v

    # Final tractor4
    tr4 = ET.SubElement(mlt, "tractor", {
        "id": "tractor4",
        "in": "00:00:00.000",
        "out": format_tc(total_dur_s)
    })
    p = ET.SubElement(tr4, "property", {"name": "kdenlive:projectTractor"})
    p.text = "1"
    ET.SubElement(tr4, "track", {
        "in": "00:00:00.000",
        "out": format_tc(total_dur_s),
        "producer": doc_uuid
    })

    # Write file
    tree = ET.ElementTree(mlt)
    ET.indent(tree, space=" ", level=0)
    tree.write(output_kdenlive, encoding="utf-8", xml_declaration=True)
    print(f"Generated Kdenlive project: {output_kdenlive}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: build_book2_kdenlive.py <wav_path> <output_kdenlive>")
        sys.exit(1)
    generate_kdenlive_project(sys.argv[1], sys.argv[2])
