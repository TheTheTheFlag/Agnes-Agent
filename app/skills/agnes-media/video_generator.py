#!/usr/bin/env python3
"""
Agnes AI Video Generator
Supports text-to-video, image-to-video, and keyframe animation.
"""

import json
import os
import time
import requests
from typing import Optional, Dict, Any, List


class AgnesVideoGenerator:
    def __init__(self, keys_file: str = None):
        # 默认读取本技能目录下的 keys.json（自包含，不依赖 ~/.hermes）
        if keys_file is None:
            keys_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json")
        self.keys_file = os.path.expanduser(keys_file)
        self.api_key = self._load_api_key()
        self.base_url = "https://api.agnes-ai.cn/v1"

    def _load_api_key(self) -> str:
        """Load API key from config file."""
        if not os.path.exists(self.keys_file):
            raise FileNotFoundError(f"Keys file not found: {self.keys_file}")
        with open(self.keys_file, "r", encoding="utf-8") as f:
            keys = json.load(f)
        return keys.get("agnes", "")

    def create(
        self,
        prompt: str,
        model: str = "agnes-video-v2.0",
        duration: str = "5",
        image: Optional[str] = None,
        mode: Optional[str] = None,
        seed: Optional[int] = None,
        num_inference_steps: Optional[int] = None,
        # v2.0 参数
        height: Optional[int] = None,
        width: Optional[int] = None,
        num_frames: Optional[int] = None,
        frame_rate: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a video generation task.

        Args:
            prompt: Video description prompt
            model: Model name (only agnes-video-v2.0 supported)
            duration: Duration in seconds
            image: Single image URL for image-to-video
            mode: Generation mode ("ti2vid" or "keyframes")
            seed: Random seed for reproducibility
            num_inference_steps: Number of inference steps
            height: Video height (default 768)
            width: Video width (default 1152)
            num_frames: Number of frames (default 121)
            frame_rate: Frame rate (default 24)

        Returns:
            Dictionary containing video_id and initial status
        """
        url = f"{self.base_url}/videos"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # agnes-video-v2.0 参数
        payload = {
            "model": model,
            "prompt": prompt,
            "height": height or 768,
            "width": width or 1152,
            "num_frames": num_frames or 121,
            "frame_rate": frame_rate or 24,
        }

        # Add single image for image-to-video
        if image:
            payload["image"] = image

        # Add mode for ti2vid or keyframes
        if mode:
            payload["mode"] = mode

        # Add optional parameters
        if seed is not None:
            payload["seed"] = seed
        if num_inference_steps is not None:
            payload["num_inference_steps"] = num_inference_steps

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        return {
            "video_id": data["video_id"],
            "status": data["status"],
            "seconds": data["seconds"],
            "size": data["size"],
        }

    def poll_result(self, video_id: str, interval: int = 8) -> Dict[str, Any]:
        """
        Poll for video completion.

        Args:
            video_id: Video ID from create()
            interval: Seconds between polls (min 8 to avoid 429)

        Returns:
            Dictionary containing video URL and metadata when completed
        """
        query_url = "https://api.agnes-ai.cn/agnesapi"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        while True:
            response = requests.get(
                query_url, params={"video_id": video_id}, headers=headers, timeout=30
            )
            response.raise_for_status()
            data = response.json()

            status = data.get("status")
            if status == "completed":
                return {
                    "url": data.get("url") or data.get("remixed_from_video_id"),
                    "progress": data.get("progress"),
                    "error": None,
                }
            elif status == "failed":
                raise Exception(f"Video failed: {data.get('error')}")
            # else: queued or processing, continue polling
            time.sleep(interval)

    def generate(
        self,
        prompt: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate a video and wait for completion.

        Args:
            prompt: Video description prompt
            **kwargs: Arguments passed to create() and poll_result()

        Returns:
            Dictionary containing video URL and metadata
        """
        # Create the video task
        task_result = self.create(prompt, **kwargs)
        print(f"Video created: {task_result['video_id']}, status: {task_result['status']}")

        # Poll until complete
        result = self.poll_result(task_result["video_id"])
        print(f"Video completed! URL: {result['url']}")

        return {
            "video_id": task_result["video_id"],
            "url": result["url"],
            "duration_seconds": task_result["seconds"],
        }


if __name__ == "__main__":
    # Example usage
    generator = AgnesVideoGenerator()
    result = generator.generate(
        prompt="A cat walking on the beach at sunset, soft ocean waves, warm golden lighting, realistic motion",
        height=768,
        width=1152,
        num_frames=121,
    )
    print(f"Video URL: {result['url']}")
