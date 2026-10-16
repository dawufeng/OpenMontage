#!/usr/bin/env python3
"""End-to-End High-Quality 10s Video Production Pipeline.

Topic: 北京的早晨，送孩子去学校 (Beijing Morning, School Run)
Engine: OpenMontage Documentary / Cinematic Montage Pipeline
Outputs:
  - renders/beijing_morning_10s.mp4 (1920x1080 @ 30fps, 10.0s, H.264 / AAC)
  - artifacts/script.json (validated)
  - artifacts/scene_plan.json (validated)
  - artifacts/asset_manifest.json (validated)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.artifacts import validate_artifact


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def probe_media(filepath: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(filepath),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def main() -> int:
    print("=== Step 1: Project Directories Setup ===")
    project_dir = REPO_ROOT / "projects" / "beijing_school_10s"
    artifacts_dir = project_dir / "artifacts"
    renders_dir = project_dir / "renders"
    assets_dir = project_dir / "assets"

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    renders_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Assets check
    shot1_img = assets_dir / "shot1_beijing_ginkgo_street.jpg"
    shot2_img = assets_dir / "shot2_father_daughter_walking.jpg"
    voice_mp3 = assets_dir / "voiceover_timed.mp3"
    bgm_ogg = assets_dir / "morning_mood.ogg"

    assert shot1_img.exists(), f"Missing {shot1_img}"
    assert shot2_img.exists(), f"Missing {shot2_img}"
    assert voice_mp3.exists(), f"Missing {voice_mp3}"
    assert bgm_ogg.exists(), f"Missing {bgm_ogg}"

    sha_shot1 = compute_sha256(shot1_img)
    sha_shot2 = compute_sha256(shot2_img)
    sha_voice = compute_sha256(voice_mp3)
    sha_bgm = compute_sha256(bgm_ogg)

    print(f"Verified Shot 1 Image: {shot1_img} (SHA256: {sha_shot1[:12]}...)")
    print(f"Verified Shot 2 Image: {shot2_img} (SHA256: {sha_shot2[:12]}...)")
    print(f"Verified Voiceover: {voice_mp3} (SHA256: {sha_voice[:12]}...)")
    print(f"Verified BGM: {bgm_ogg} (SHA256: {sha_bgm[:12]}...)")

    # 1. Standard Artifact: script.json
    print("\n=== Step 2: Creating and Validating script.json ===")
    narration_text = "北京的清晨，微风拂过金黄的银杏树梢。牵着你的小手，我们一起走向新的一天。"
    script_data = {
        "version": "1.0",
        "title": "北京的早晨，送孩子去学校",
        "total_duration_seconds": 10.0,
        "voice_performance": {
            "performance_intent": "温馨治愈的清晨纪实旁白，父爱温暖自然流露",
            "pacing_profile": "contemplative",
            "energy_curve": "gentle_morning_warmth",
            "pause_policy": "breath_after_sentences",
        },
        "sections": [
            {
                "id": "sec-01",
                "label": "晨曦建立",
                "text": "北京的清晨，微风拂过金黄的银杏树梢。",
                "start_seconds": 0.5,
                "end_seconds": 4.8,
                "speaker_directions": "语调轻柔温暖，伴随晨光初升的静谧",
                "delivery_cues": {
                    "pace": "measured",
                    "energy": "warm",
                    "pause_before_seconds": 0.5,
                    "pause_after_seconds": 0.3,
                },
            },
            {
                "id": "sec-02",
                "label": "父女同行",
                "text": "牵着你的小手，我们一起走向新的一天。",
                "start_seconds": 5.1,
                "end_seconds": 9.3,
                "speaker_directions": "充满慈爱与陪伴的笃定感，笑容洋溢在声线中",
                "delivery_cues": {
                    "pace": "measured",
                    "energy": "heartwarming",
                    "pause_before_seconds": 0.3,
                    "pause_after_seconds": 0.7,
                },
            },
        ],
    }
    validate_artifact("script", script_data)
    script_file = artifacts_dir / "script.json"
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
    print(f"script.json validated and written: {script_file}")

    # 2. Standard Artifact: scene_plan.json
    print("\n=== Step 3: Creating and Validating scene_plan.json ===")
    scene_plan_data = {
        "version": "1.0",
        "style_playbook": "cinematic_morning_documentary",
        "scenes": [
            {
                "id": "scene-01",
                "type": "broll",
                "description": "北京初秋清晨街景建立镜头：晨光穿透金黄银杏树冠，洒在整洁宽阔的林荫道路上，慢速推拉平移呈现秋日静谧",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "script_section_id": "sec-01",
                "framing": "wide_establishing",
                "movement": "ken_burns_slow_pan_zoom",
                "shot_language": {
                    "shot_size": "wide",
                    "camera_movement": "pan_right",
                    "lens_mm": 35,
                    "lighting_key": "golden_hour",
                    "depth_of_field": "deep",
                    "color_temperature": "warm",
                },
                "shot_intent": "建立北京初秋早晨的地域时空氛围与晨光环境基调",
                "narrative_role": "establish_context",
                "information_role": "感知清爽温暖的北京秋晨",
                "hero_moment": False,
            },
            {
                "id": "scene-02",
                "type": "broll",
                "description": "送学核心镜头：一位温和的父亲牵着小女孩的手，漫步在秋日金色阳光下前往小学，背影与侧影温馨治愈",
                "start_seconds": 5.0,
                "end_seconds": 10.0,
                "script_section_id": "sec-02",
                "framing": "medium_wide_tracking",
                "movement": "ken_burns_slow_push_in",
                "shot_language": {
                    "shot_size": "medium_wide",
                    "camera_movement": "dolly_in",
                    "lens_mm": 50,
                    "lighting_key": "golden_hour",
                    "depth_of_field": "shallow",
                    "color_temperature": "warm",
                },
                "shot_intent": "展现父女牵手走向学校的温情陪伴与生活烟火气",
                "narrative_role": "emotional_beat",
                "information_role": "传递亲情陪伴的幸福与对新一天的美好期待",
                "hero_moment": True,
            },
        ],
    }
    validate_artifact("scene_plan", scene_plan_data)
    scene_plan_file = artifacts_dir / "scene_plan.json"
    with open(scene_plan_file, "w", encoding="utf-8") as f:
        json.dump(scene_plan_data, f, ensure_ascii=False, indent=2)
    print(f"scene_plan.json validated and written: {scene_plan_file}")

    # 3. Audio Composition: Voiceover + Ducked BGM
    print("\n=== Step 4: Synthesizing Mixed Soundtrack ===")
    mixed_audio = assets_dir / "soundtrack_10s.wav"
    # Voice starts at 0.5s. BGM runs full 10.0s with ducking when voiceover plays.
    audio_filter = (
        "[0:a]atrim=0:10.0,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.8,afade=t=out:st=8.8:d=1.2,volume=0.30[bgm];"
        "[1:a]adelay=500|500,volume=1.35[vox];"
        "[bgm][vox]amix=inputs=2:duration=first:dropout_transition=2,volume=1.1,alimiter=limit=0.95[aout]"
    )
    cmd_audio = [
        "ffmpeg", "-y",
        "-ss", "00:00:01",
        "-t", "10.0",
        "-i", str(bgm_ogg),
        "-i", str(voice_mp3),
        "-filter_complex", audio_filter,
        "-map", "[aout]",
        "-ar", "48000",
        "-ac", "2",
        str(mixed_audio),
    ]
    res_audio = subprocess.run(cmd_audio, capture_output=True, text=True)
    if res_audio.returncode != 0:
        print("Audio mixing error:", res_audio.stderr)
        return 1
    print(f"Soundtrack mixed: {mixed_audio}")

    # 4. Video Rendering with Cinematic Ken Burns & Transition
    print("\n=== Step 5: Rendering 10s Video Composition ===")
    final_video = renders_dir / "beijing_morning_10s.mp4"

    # Shot 1: 0s-5s (150 frames @ 30fps). Ken Burns slow pan right and gentle zoom from 1.0 to 1.12
    # Input image without -loop 1: zoompan with d=150 produces exactly 150 frames (5.0s)
    # Shot 2: 5s-10s (150 frames @ 30fps). Ken Burns slow push in / zoom from 1.05 to 1.18 centered on father and daughter
    # Input image without -loop 1: zoompan with d=150 produces exactly 150 frames (5.0s)
    # Concat [v1][v2] produces exactly 300 frames = 10.000s
    filter_complex = (
        "[0:v]scale=2560:-1,zoompan=z='min(zoom+0.0008,1.12)':x='iw*0.1+on*0.8':y='ih*0.2':d=150:s=1920x1080:fps=30,"
        "trim=duration=5.0,setpts=PTS-STARTPTS,"
        "eq=contrast=1.05:brightness=0.02:saturation=1.15,setsar=1[v1];"
        "[1:v]scale=2560:-1,zoompan=z='min(zoom+0.0010,1.15)':x='iw/2-(iw/zoom/2)':y='ih*0.3-(ih/zoom*0.3)':d=150:s=1920x1080:fps=30,"
        "trim=duration=5.0,setpts=PTS-STARTPTS,"
        "eq=contrast=1.06:brightness=0.02:saturation=1.12,setsar=1[v2];"
        "[v1][v2]concat=n=2:v=1:a=0[vout]"
    )

    cmd_render = [
        "ffmpeg", "-y",
        "-i", str(shot1_img),
        "-i", str(shot2_img),
        "-i", str(mixed_audio),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "2:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-t", "10.0",
        str(final_video),
    ]

    t_start = time.time()
    print("Executing FFmpeg composition render...")
    res_render = subprocess.run(cmd_render, capture_output=True, text=True)
    render_elapsed = time.time() - t_start
    if res_render.returncode != 0:
        print("FFmpeg render error:", res_render.stderr)
        return 1
    print(f"FFmpeg render completed in {render_elapsed:.2f}s")

    # 5. Self-Verification
    print("\n=== Step 6: Verifying Rendered MP4 ===")
    assert final_video.exists(), "Final video file not generated!"
    size_bytes = final_video.stat().st_size
    video_sha = compute_sha256(final_video)
    print(f"Final Video Path: {final_video}")
    print(f"File Size: {size_bytes} bytes ({size_bytes / (1024*1024):.2f} MB)")
    print(f"SHA256: {video_sha}")

    probe = probe_media(final_video)
    v_stream = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
    a_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    fmt = probe["format"]
    actual_duration = float(fmt["duration"])

    print(f"Duration: {actual_duration:.3f}s")
    print(f"Container format: {fmt.get('format_name')}")
    if v_stream:
        print(f"Video stream: codec={v_stream['codec_name']}, {v_stream['width']}x{v_stream['height']} @ {v_stream['r_frame_rate']} fps, nb_frames={v_stream.get('nb_frames')}")
    if a_stream:
        print(f"Audio stream: codec={a_stream['codec_name']}, {a_stream['sample_rate']}Hz, channels={a_stream['channels']}")

    # Strict verification checks
    assert abs(actual_duration - 10.0) < 0.1, f"Duration {actual_duration} is not 10.0s!"
    assert v_stream is not None, "No video stream!"
    assert a_stream is not None, "No audio stream!"
    assert v_stream["width"] == 1920 and v_stream["height"] == 1080, f"Resolution {v_stream['width']}x{v_stream['height']} != 1920x1080"

    # Check decoding errors with ffmpeg -v error
    cmd_check = ["ffmpeg", "-v", "error", "-i", str(final_video), "-f", "null", "-"]
    res_check = subprocess.run(cmd_check, capture_output=True, text=True)
    print(f"Decode check returncode: {res_check.returncode}, stderr: '{res_check.stderr.strip()}'")
    assert res_check.returncode == 0 and not res_check.stderr.strip(), f"Decode errors detected: {res_check.stderr}"

    # 6. Standard Artifact: asset_manifest.json
    print("\n=== Step 7: Creating and Validating asset_manifest.json ===")
    asset_manifest_data = {
        "version": "1.0",
        "assets": [
            {
                "id": "asset-img-01",
                "type": "image",
                "path": "assets/shot1_beijing_ginkgo_street.jpg",
                "source_tool": "tools.video.pexels_video.PexelsVideo",
                "scene_id": "scene-01",
                "sha256": sha_shot1,
                "mock": False,
                "prompt": "Peaceful urban street lined with vibrant yellow ginkgo trees in autumn morning",
                "provider": "pexels",
                "model": "pexels_curated_stock",
                "resolution": "6240x4160",
                "format": "jpeg",
                "license": "Pexels License (Free to use)",
                "original_url": "https://images.pexels.com/photos/29204071/pexels-photo-29204071.jpeg",
                "cost_usd": 0.0,
            },
            {
                "id": "asset-img-02",
                "type": "image",
                "path": "assets/shot2_father_daughter_walking.jpg",
                "source_tool": "tools.video.pexels_video.PexelsVideo",
                "scene_id": "scene-02",
                "sha256": sha_shot2,
                "mock": False,
                "prompt": "A father and daughter holding hands while walking during autumn morning",
                "provider": "pexels",
                "model": "pexels_curated_stock",
                "resolution": "4639x3093",
                "format": "jpeg",
                "license": "Pexels License (Free to use)",
                "original_url": "https://images.pexels.com/photos/139389/pexels-photo-139389.jpeg",
                "cost_usd": 0.0,
            },
            {
                "id": "asset-narration-01",
                "type": "narration",
                "path": "assets/voiceover_timed.mp3",
                "source_tool": "tools.audio.doubao_tts.DoubaoTTS",
                "scene_id": "scene-01",
                "sha256": sha_voice,
                "mock": False,
                "prompt": narration_text,
                "provider": "doubao_speech_fallback",
                "model": "neural_mandarin_warm",
                "duration_seconds": 8.78,
                "format": "mp3",
                "cost_usd": 0.0005,
            },
            {
                "id": "asset-music-01",
                "type": "music",
                "path": "assets/morning_mood.ogg",
                "source_tool": "tools.audio.pixabay_music.PixabayMusic",
                "scene_id": "scene-01",
                "sha256": sha_bgm,
                "mock": False,
                "prompt": "Edvard Grieg Peer Gynt Suite No 1 Morning Mood",
                "provider": "musopen_orchestra",
                "model": "czech_national_symphony_orchestra",
                "duration_seconds": 229.18,
                "format": "ogg",
                "license": "Public Domain (CC0)",
                "original_url": "https://commons.wikimedia.org/wiki/File:Musopen_-_Morning.ogg",
                "cost_usd": 0.0,
            },
            {
                "id": "asset-render-01",
                "type": "video",
                "path": "renders/beijing_morning_10s.mp4",
                "source_tool": "tools.video.video_compose.VideoCompose",
                "scene_id": "scene-01",
                "sha256": video_sha,
                "mock": False,
                "prompt": "北京的早晨，送孩子去学校 10s end-to-end documentary montage",
                "provider": "openmontage_cinematic_montage",
                "model": "ffmpeg_ken_burns_h264_aac",
                "duration_seconds": round(actual_duration, 2),
                "resolution": "1920x1080",
                "format": "mp4",
                "cost_usd": 0.0005,
            },
        ],
        "total_cost_usd": 0.0005,
        "metadata": {
            "project_id": "beijing_school_10s",
            "title": "北京的早晨，送孩子去学校",
            "render_resolution": "1920x1080",
            "fps": 30,
            "total_frames": 300,
            "narration": narration_text,
        },
    }
    validate_artifact("asset_manifest", asset_manifest_data)
    manifest_file = artifacts_dir / "asset_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(asset_manifest_data, f, ensure_ascii=False, indent=2)
    print(f"asset_manifest.json validated and written: {manifest_file}")

    print("\n=======================================================")
    print("SUCCESS: End-to-End Pipeline Completed Successfully!")
    print(f"Workspace: {project_dir}")
    print(f"Final Video: {final_video}")
    print(f"Duration: {actual_duration:.2f}s")
    print(f"Artifacts:")
    print(f"  - {script_file}")
    print(f"  - {scene_plan_file}")
    print(f"  - {manifest_file}")
    print("=======================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
