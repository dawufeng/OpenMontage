"""Seedance 2.0 (ByteDance) video generation via Volcengine Ark (火山方舟) API."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class VolcengineSeedance(BaseTool):
    name = "volcengine_seedance"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "seedance"  # Scorer matches provider="seedance"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set VOLCENGINE_ARK_API_KEY (or ARK_API_KEY) to your Volcengine Ark API key.\n"
        "Optionally set VOLCENGINE_SEEDANCE_MODEL (default: doubao-seedance-2-0-260128) and VOLCENGINE_ARK_BASE_URL."
    )
    agent_skills = ["seedance-2-0", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "reference_to_video": True,
        "multiple_reference_images": True,
        "reference_image": True,
        "native_audio": True,
        "cinematic_quality": True,
        "camera_direction": True,
        "lip_sync": True,
        "multi_shot": True,
        "aspect_ratio": True,
        "seed": True,
    }
    best_for = [
        "preferred premium video gen when Volcengine Ark API is configured",
        "cinematic trailers, teasers, and high-fidelity clips with native synchronized audio",
        "director-level camera control and multi-shot editing in a single generation",
        "lip-sync from quoted dialogue in prompts",
        "consistent character identity across shots",
    ]
    not_good_for = ["offline generation", "budget-constrained projects"]
    fallback_tools = ["seedance_video", "seedance_replicate", "veo_video", "kling_video", "minimax_video"]
    quality_score = 0.95

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video", "reference_to_video"],
                "default": "text_to_video",
            },
            "model_variant": {
                "type": "string",
                "enum": ["standard", "fast"],
                "default": "fast",
            },
            "duration": {
                "type": "string",
                "enum": ["auto", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
                "default": "5",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                "default": "16:9",
            },
            "resolution": {
                "type": "string",
                "enum": ["480p", "720p", "1080p"],
                "default": "720p",
            },
            "generate_audio": {
                "type": "boolean",
                "default": True,
            },
            "image_url": {
                "type": "string",
                "description": "Start frame image URL for image_to_video",
            },
            "image_path": {
                "type": "string",
                "description": "Local start-frame path for image_to_video.",
            },
            "reference_image_urls": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "seed": {"type": "integer"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "operation", "duration", "seed"]
    side_effects = ["writes video file to output_path", "calls Volcengine Ark API"]
    user_visible_verification = [
        "Watch generated clip for motion coherence, audio sync, and visual quality"
    ]

    def _get_api_key(self) -> str | None:
        return os.environ.get("VOLCENGINE_ARK_API_KEY") or os.environ.get("ARK_API_KEY")

    def _get_base_url(self) -> str:
        return os.environ.get("VOLCENGINE_ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"

    def _get_model(self) -> str:
        variant = os.environ.get("VOLCENGINE_SEEDANCE_MODEL")
        if variant:
            return variant
        # Default to fast variant; standard is "doubao-seedance-2-0-260128"
        return "doubao-seedance-2-0-fast-260128"

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        variant = inputs.get("model_variant", "standard")
        duration = inputs.get("duration", "5")
        secs = 5 if duration == "auto" else int(duration)
        rate = 0.24 if variant == "fast" else 0.30
        return round(rate * secs, 2)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 60.0 if inputs.get("model_variant") == "fast" else 120.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="VOLCENGINE_ARK_API_KEY or ARK_API_KEY not set. " + self.install_instructions,
            )

        import requests

        start = time.time()
        operation = inputs.get("operation", "text_to_video")
        base_url = self._get_base_url().rstrip('/')
        model = self._get_model()

        # Build content list
        content: list[dict[str, Any]] = [
            {"type": "text", "text": inputs["prompt"]}
        ]

        # Add image input if present
        if operation == "image_to_video":
            image_url = inputs.get("image_url")
            if not image_url and inputs.get("image_path"):
                try:
                    from tools.video._shared import upload_image_fal
                    image_url = upload_image_fal(inputs["image_path"])
                except Exception as e:
                    return ToolResult(success=False, error=f"Failed to upload local image to fal: {e}")
            if image_url:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "first_frame"
                })

        elif operation == "reference_to_video":
            ref_urls = list(inputs.get("reference_image_urls") or [])
            for p in inputs.get("reference_image_paths") or []:
                try:
                    from tools.video._shared import upload_image_fal
                    ref_urls.append(upload_image_fal(p))
                except Exception as e:
                    return ToolResult(success=False, error=f"Failed to upload local reference image to fal: {e}")

            for url in ref_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": "reference_image"
                })

        # Aspect ratio conversion
        aspect_ratio = inputs.get("aspect_ratio", "16:9")
        if aspect_ratio == "auto":
            aspect_ratio = "16:9"

        # Build payload
        payload: dict[str, Any] = {
            "model": model,
            "content": content,
            "ratio": aspect_ratio,
        }

        # Optional fields
        if inputs.get("resolution"):
            payload["resolution"] = inputs["resolution"]
        if inputs.get("duration") and inputs["duration"] != "auto":
            payload["duration"] = int(inputs["duration"])

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Submit task
        try:
            submit_url = f"{base_url}/contents/generations/tasks"
            response = requests.post(submit_url, headers=headers, json=payload, timeout=30)

            if response.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Volcengine API creation failed with code {response.status_code}: {response.text}"
                )

            task_data = response.json()
            task_id = task_data.get("id")
            if not task_id:
                return ToolResult(
                    success=False,
                    error=f"Volcengine API response did not contain task id: {task_data}"
                )

            # Poll status
            status_url = f"{base_url}/contents/generations/tasks/{task_id}"
            video_url = None
            max_polls = 120  # 10 minutes max

            for _ in range(max_polls):
                time.sleep(5)
                poll_resp = requests.get(status_url, headers=headers, timeout=15)
                if poll_resp.status_code != 200:
                    continue

                poll_data = poll_resp.json()
                status = poll_data.get("status", "").lower()

                if status == "succeeded":
                    content_out = poll_data.get("content", {})
                    if isinstance(content_out, dict):
                        video_url = content_out.get("video_url")
                    break
                elif status in ("failed", "expired", "cancelled"):
                    error_msg = poll_data.get("error", {}).get("message") or "Unknown error"
                    return ToolResult(
                        success=False,
                        error=f"Volcengine video generation failed with status '{status}': {error_msg}"
                    )

            if not video_url:
                return ToolResult(
                    success=False,
                    error="Volcengine video generation timed out."
                )

            # Download video
            video_response = requests.get(video_url, timeout=120)
            video_response.raise_for_status()

            output_path = Path(inputs.get("output_path", f"volcengine_seedance_{task_id}.mp4"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(video_response.content)

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Volcengine video generation failed with error: {e}"
            )

        from tools.video._shared import probe_output

        probed = probe_output(output_path)
        return ToolResult(
            success=True,
            data={
                "provider": "seedance",
                "gateway": "volcengine",
                "model": model,
                "prompt": inputs["prompt"],
                "operation": operation,
                "aspect_ratio": aspect_ratio,
                "output": str(output_path),
                "output_path": str(output_path),
                "format": "mp4",
                **probed,
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
