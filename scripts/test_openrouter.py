"""Test OpenRouter API with NVIDIA Nemotron 3 Nano Omni (free) model.
Usage:
    OPENROUTER_API_KEY="sk-or-v1-..." ./venv/bin/python scripts/test_openrouter.py
"""
import asyncio
import os
import sys
import json
import base64
import struct
import zlib
import httpx

# Tell pytest not to collect this CLI script as a test suite
__test__ = False

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free")

def make_sample_png():
    """Create a minimal 64x64 valid PNG image in bytes."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    rows = (b"\x00" + b"\x00\xaa\x55" * 64) * 64
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )

async def test_text_prompt(client: httpx.AsyncClient):
    print(f"\n--- Testing Text Prompt on {MODEL} ---")
    prompt = (
        "Respond strictly with valid JSON. "
        "User had 2 boiled eggs. Return json with keys: "
        "food_items (list with name, portion, calories, protein, fat, carb), "
        "total_calories, total_protein, total_fat, total_carb."
    )
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://healthy-bot.local",
        "X-Title": "HealthyBot",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
    }

    try:
        resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60.0)
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error response: {resp.text}")
            return False
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        print(f"Response content:\n{content}")
        return True
    except Exception as e:
        print(f"Exception: {e}")
        return False

async def test_image_prompt(client: httpx.AsyncClient):
    print(f"\n--- Testing Image (Vision) Prompt on {MODEL} ---")
    img_bytes = make_sample_png()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    prompt = (
        "What is in this image? Return valid JSON with key 'description'. "
        "If you cannot see the image, say so."
    )
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://healthy-bot.local",
        "X-Title": "HealthyBot",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.2,
    }

    try:
        resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60.0)
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error response: {resp.text}")
            return False
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        print(f"Response content:\n{content}")
        return True
    except Exception as e:
        print(f"Exception: {e}")
        return False

async def main():
    if not OPENROUTER_API_KEY:
        print("ERROR: Please set OPENROUTER_API_KEY environment variable.")
        print("You can get a free key at: https://openrouter.ai/keys")
        sys.exit(1)

    async with httpx.AsyncClient() as client:
        ok_text = await test_text_prompt(client)
        if ok_text:
            await test_image_prompt(client)

if __name__ == "__main__":
    asyncio.run(main())
