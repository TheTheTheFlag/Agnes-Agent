#!/usr/bin/env python3
"""
Agnes AI Image Generator
Supports text-to-image and image-to-image generation.
"""

import json
import os
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any


class AgnesImageGenerator:
    def __init__(self, keys_file: str = "~/.hermes/agnes_keys.json"):
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

    def generate(
        self,
        prompt: str,
        model: str = "agnes-image-2.1-flash",
        size: str = "1K",
        ratio: str = "1:1",
        image: Optional[List[str]] = None,
        response_format: str = "url",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an image.

        Args:
            prompt: Image description prompt
            model: Model name (agnes-image-2.1-flash or agnes-image-2.0-flash)
            size: Output size (1K, 2K, 3K, 4K or exact like 1024x1024)
            ratio: Aspect ratio (1:1, 16:9, 9:16, etc.)
            image: Base64 data URIs for image-to-image (optional)
            response_format: "url" or "b64_json"
            extra_params: Additional parameters to pass to API

        Returns:
            Dictionary containing image URL and metadata
        """
        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "ratio": ratio,
        }

        # Add image for image-to-image
        if image:
            payload["image"] = image

        # Add response format via extra_body
        extra_body = {"response_format": response_format}
        if extra_params:
            extra_body.update(extra_params)
        payload["extra_body"] = extra_body

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        return {
            "url": data["data"][0]["url"],
            "b64_json": data["data"][0].get("b64_json"),
            "prompt": prompt,
            "model": model,
        }

    def generate_batch(
        self,
        prompts: List[str],
        model: str = "agnes-image-2.1-flash",
        size: str = "1K",
        ratio: str = "1:1",
        max_workers: int = 5,
    ) -> List[Dict[str, Any]]:
        """Generate multiple images in parallel."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        tasks = [(p, model, size, ratio) for p in prompts]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.generate, *t): t for t in tasks}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"error": str(e)})
        return results


if __name__ == "__main__":
    # Example usage
    generator = AgnesImageGenerator()
    result = generator.generate(
        prompt="A beautiful sunset over the ocean, photorealistic",
        size="1K",
        ratio="16:9",
    )
    print(f"Image URL: {result['url']}")