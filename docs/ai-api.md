```bash
ai 聊天获取接口如下：
https://x666.me
预览：https://x666.me/v1/chat/completions
密钥：sk-pR6ZsovTS0l7DWlPl1sEl6Pf8duPCJO8poO7tiWrwPHO91cX
模型：gemini-2.5-pro-1m


```


```bash
#!/usr/bin/env python3
"""
Batch-convert transcripts using the provided chat completion API.

Default settings follow the user's instructions:
- Input folder: 需要转换的
- Output folder: 需要转换的结果
- System prompt file: 文字跟进prompt.txt
- Model: gemini-2.5-pro-1m
- Rate limit: 1 request/minute
"""

import argparse
import json
import os
import time
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

API_URL = "https://x666.me/v1/chat/completions"
DEFAULT_MODEL = "gemini-2.5-pro-1m"
# Fallback key from the user's instructions; environment variable takes priority.
DEFAULT_API_KEY = "sk-pR6ZsovTS0l7DWlPl1sEl6Pf8duPCJO8poO7tiWrwPHO91cX"


def read_text(path: Path) -> str:
    """Read text as UTF-8 with replacement to avoid crashes on mixed encodings."""
    return path.read_text(encoding="utf-8", errors="replace")


def load_prompt(path: Path) -> str:
    prompt = read_text(path).strip()
    if not prompt:
        raise ValueError(f"Prompt file {path} is empty.")
    return prompt


def call_ai(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    api_key: str,
    temperature: float,
    timeout: int,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": temperature,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Cloudflare in front of x666.me may block requests without a common UA.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    }
    request = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc
    except socket.timeout as exc:
        raise RuntimeError(f"Request timed out: {exc}") from exc

    try:
        parsed = json.loads(raw)
        return parsed["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unexpected response: {raw!r}") from exc


def iter_input_files(folder: Path) -> Iterable[Path]:
    return sorted(p for p in folder.glob("*.txt") if p.is_file())


def process_files(
    *,
    input_dir: Path,
    output_dir: Path,
    system_prompt: str,
    model: str,
    api_key: str,
    delay: int,
    limit: int,
    temperature: float,
    overwrite: bool,
    retries: int,
    retry_wait: int,
    timeout: int,
) -> int:
    files = list(iter_input_files(input_dir))
    output_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    for idx, src in enumerate(files):
        if limit and processed >= limit:
            break

        dest = output_dir / src.name
        if dest.exists() and not overwrite:
            print(f"[skip] {dest} already exists.")
            continue

        text = read_text(src)
        print(f"[{processed + 1}/{len(files)}] Sending: {src.name}")

        attempt = 0
        while True:
            attempt += 1
            try:
                result = call_ai(
                    model=model,
                    system_prompt=system_prompt,
                    user_text=text,
                    api_key=api_key,
                    temperature=temperature,
                    timeout=timeout,
                )
                dest.write_text(result, encoding="utf-8")
                print(f"[done]  Saved to: {dest}")
                processed += 1
                break
            except Exception as exc:  # noqa: BLE001
                if attempt > retries:
                    print(f"[fail] {src.name} after {retries} retries: {exc}")
                    break
                print(f"[retry {attempt}/{retries}] {src.name} due to: {exc}")
                time.sleep(retry_wait)

        remaining = (limit - processed) if limit else (len(files) - idx - 1)
        if remaining > 0 and delay > 0:
            print(f"[wait] Sleeping {delay} seconds to respect rate limit...")
            time.sleep(delay)

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert transcripts using the x666 chat completion API.")
    parser.add_argument("--input-dir", default="需要转换的", type=Path, help="Folder containing source .txt files.")
    parser.add_argument("--output-dir", default="需要转换的结果", type=Path, help="Folder to write converted files.")
    parser.add_argument("--prompt-file", default="文字跟进prompt.txt", type=Path, help="System prompt file.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name.")
    parser.add_argument("--temperature", default=0.3, type=float, help="Sampling temperature.")
    parser.add_argument("--delay", default=60, type=int, help="Seconds to wait between API calls.")
    parser.add_argument("--timeout", default=150, type=int, help="Per-request timeout in seconds.")
    parser.add_argument("--limit", default=0, type=int, help="Process at most N files (0 = no limit).")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--retries", default=3, type=int, help="Number of retries per file on failure.")
    parser.add_argument("--retry-wait", default=15, type=int, help="Seconds to wait between retries for a file.")
    parser.add_argument("--key", dest="api_key", help="API key (overrides env and default).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.getenv("X666_API_KEY") or DEFAULT_API_KEY
    if not api_key:
        raise SystemExit("API key is missing. Provide --key or set X666_API_KEY.")

    system_prompt = load_prompt(args.prompt_file)
    processed = process_files(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        system_prompt=system_prompt,
        model=args.model,
        api_key=api_key,
        delay=args.delay,
        limit=args.limit,
        temperature=args.temperature,
        overwrite=args.overwrite,
        retries=args.retries,
        retry_wait=args.retry_wait,
        timeout=args.timeout,
    )
    print(f"Completed. Converted {processed} file(s).")


if __name__ == "__main__":
    main()

```

