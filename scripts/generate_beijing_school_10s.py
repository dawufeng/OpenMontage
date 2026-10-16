#!/usr/bin/env python3
"""End-to-End AI Video Generation Pipeline for Beijing Morning School Run.

Executes SeedanceArkVideo tool against Volcengine Ark Seedance 2.0 endpoint,
produces standard OpenMontage artifacts (script.json, scene_plan.json, asset_manifest.json),
and verifies the rendered 10-second video.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure OpenMontage root is in python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from lib.checkpoint import init_project
from schemas.artifacts import validate_artifact
from tools.video.seedance_ark import SeedanceArkVideo


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
    print("=== Step 1: Initializing Project Workspace ===")
    project_id = "beijing_school_10s"
    project_dir = init_project(
        project_id=project_id,
        title="北京的早晨，送孩子去学校",
        pipeline_type="cinematic",
    )
    print(f"Project directory initialized at: {project_dir}")

    artifacts_dir = project_dir / "artifacts"
    renders_dir = project_dir / "renders"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    renders_dir.mkdir(parents=True, exist_ok=True)

    # 1. Standard Artifact: script.json
    print("\n=== Step 2: Creating and Validating script.json ===")
    prompt_text = (
        "北京初秋清晨，金黄银杏叶在阳光下闪烁，柔和晨光斜射在干净街道上。"
        "一位温和的父亲牵着背着红色小书包、扎着双马尾的小女孩，两人有说有笑走在前往小学的林荫道上，"
        "偶有骑自行车的市民经过，充满生活气息，电影级暖色调调色，真实细节，4K质感，温馨治愈，微风吹拂"
    )
    script_data = {
        "version": "1.0",
        "title": "北京的早晨，送孩子去学校",
        "total_duration_seconds": 10.0,
        "sections": [
            {
                "id": "sec-01",
                "label": "送学晨景",
                "text": prompt_text,
                "start_seconds": 0.0,
                "end_seconds": 10.0,
            }
        ],
    }
    validate_artifact("script", script_data)
    script_file = artifacts_dir / "script.json"
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
    print(f"script.json written and schema-validated: {script_file}")

    # 2. Standard Artifact: scene_plan.json
    print("\n=== Step 3: Creating and Validating scene_plan.json ===")
    scene_plan_data = {
        "version": "1.0",
        "scenes": [
            {
                "id": "scene-01",
                "type": "generated",
                "description": prompt_text,
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "script_section_id": "sec-01",
                "shot_language": {
                    "shot_size": "medium_wide",
                    "camera_movement": "tracking_right",
                    "lighting_key": "golden_hour",
                    "depth_of_field": "shallow",
                    "color_temperature": "warm",
                },
                "hero_moment": True,
            }
        ],
    }
    validate_artifact("scene_plan", scene_plan_data)
    scene_plan_file = artifacts_dir / "scene_plan.json"
    with open(scene_plan_file, "w", encoding="utf-8") as f:
        json.dump(scene_plan_data, f, ensure_ascii=False, indent=2)
    print(f"scene_plan.json written and schema-validated: {scene_plan_file}")

    # 3. Call SeedanceArkVideo to generate video
    print("\n=== Step 4: Executing SeedanceArkVideo Pipeline ===")
    output_video_path = renders_dir / "beijing_morning_10s.mp4"
    tool = SeedanceArkVideo()
    print(f"Using Base URL: {tool._get_base_url()}")
    print(f"Tool Status: {tool.get_status()}")

    video_inputs = {
        "prompt": prompt_text,
        "duration": 10,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "model_variant": "standard",
        "generate_audio": True,
        "output_path": str(output_video_path),
    }

    print(f"Submitting video task with inputs: {json.dumps(video_inputs, ensure_ascii=False, indent=2)}")
    t0 = time.time()
    result = tool.execute(video_inputs)
    t_elapsed = round(time.time() - t0, 2)
    print(f"Tool execution returned in {t_elapsed}s. Success: {result.success}")

    if not result.success:
        print(f"ERROR: Video generation failed: {result.error}")
        if result.data:
            print(f"Details: {result.data}")
        return 1

    print("Generation succeeded!")
    print(f"Task ID: {result.data.get('task_id')}")
    print(f"Model: {result.data.get('model')}")
    print(f"Cost USD: {result.cost_usd}")
    print(f"Estimated Cost CNY: {result.data.get('estimated_cost_cny')}")

    # 4. Self-verify generated mp4
    print("\n=== Step 5: Self-Verifying Rendered Video ===")
    if not output_video_path.is_file():
        print(f"ERROR: Expected output file does not exist: {output_video_path}")
        return 1

    file_size_bytes = output_video_path.stat().st_size
    file_sha256 = compute_sha256(output_video_path)
    print(f"File path: {output_video_path}")
    print(f"File size: {file_size_bytes} bytes ({file_size_bytes / (1024*1024):.2f} MB)")
    print(f"SHA256: {file_sha256}")

    if file_size_bytes < 100_000:
        print("ERROR: File size suspiciously small for a 10s video!")
        return 1

    probe_data = probe_media(output_video_path)
    streams = probe_data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    format_info = probe_data.get("format", {})
    actual_duration = float(format_info.get("duration", 0.0))
    print(f"Container format: {format_info.get('format_name')}")
    print(f"Duration: {actual_duration:.2f}s")
    if v_stream:
        print(f"Video stream: codec={v_stream.get('codec_name')}, resolution={v_stream.get('width')}x{v_stream.get('height')}, fps={v_stream.get('r_frame_rate')}")
    if a_stream:
        print(f"Audio stream: codec={a_stream.get('codec_name')}, sample_rate={a_stream.get('sample_rate')}, channels={a_stream.get('channels')}")

    # 5. Standard Artifact: asset_manifest.json
    print("\n=== Step 6: Creating and Validating asset_manifest.json ===")
    cost_usd = float(result.cost_usd or 0.0)
    asset_manifest_data = {
        "version": "1.0",
        "assets": [
            {
                "id": "asset-video-01",
                "type": "video",
                "path": f"renders/{output_video_path.name}",
                "source_tool": "tools.video.seedance_ark.SeedanceArkVideo",
                "scene_id": "scene-01",
                "provider": "ark",
                "model": str(result.data.get("model") or "doubao-seedance-2-0-260128"),
                "prompt": prompt_text,
                "duration_seconds": actual_duration if actual_duration > 0 else 10.0,
                "resolution": f"{v_stream.get('width')}x{v_stream.get('height')}" if v_stream else "720p",
                "format": "mp4",
                "mock": False,
                "cost_usd": round(cost_usd, 6),
                "sha256": file_sha256,
            }
        ],
        "total_cost_usd": round(cost_usd, 6),
    }
    validate_artifact("asset_manifest", asset_manifest_data)
    manifest_file = artifacts_dir / "asset_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(asset_manifest_data, f, ensure_ascii=False, indent=2)
    print(f"asset_manifest.json written and schema-validated: {manifest_file}")

    print("\n=== SUCCESS: Pipeline Completed End-to-End ===")
    print(f"Workspace: {project_dir}")
    print(f"Script: {script_file}")
    print(f"Scene Plan: {scene_plan_file}")
    print(f"Asset Manifest: {manifest_file}")
    print(f"Rendered Video: {output_video_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
