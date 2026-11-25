#!/usr/bin/env python3
"""Run the complete Instagram comment workflow from a single post/Reel URL.

Usage:
    ./venv/bin/python backend/pipeline.py "https://www.instagram.com/reel/SHORTCODE/"
    ./venv/bin/python backend/pipeline.py "URL" --show-browser

Intermediate JSON and the final bad-comments text file are saved in results/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
VENV_PYTHON = next(
    (
        candidate
        for candidate in (
            PROJECT_DIR / "venv" / "bin" / "python",
            BACKEND_DIR / "venv" / "bin" / "python",
        )
        if candidate.exists()
    ),
    Path(sys.executable),
)
PYTHON = str(VENV_PYTHON)


def shortcode_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"p", "reel", "tv"}:
        return re.sub(r"[^A-Za-z0-9_-]", "_", parts[1])
    return "instagram_comments"


def has_groq_key() -> bool:
    if os.environ.get("GROQ_API_KEY", "").strip():
        return True
    for env_file in (PROJECT_DIR / ".env", BACKEND_DIR / ".env"):
        if not env_file.is_file():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "GROQ_API_KEY":
                    value = value.strip().strip("\"'")
                    if value and value != "your-api-key":
                        return True
        except OSError:
            continue
    return False


def run_step(title: str, command: list[str]) -> int:
    print(f"\n== {title} ==", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_DIR, check=False)
    if completed.returncode:
        print(f"Step failed ({completed.returncode}): {title}", file=sys.stderr)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape, clean, and classify Instagram comments from one URL."
    )
    parser.add_argument("url", help="Instagram post, Reel, or video URL")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser if you need to sign in to Instagram.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results"),
        help="Directory for intermediate and final files (default: results).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Comments sent per Groq request (default: 10).",
    )
    parser.add_argument(
        "--model",
        help="Optional Groq model ID override passed to check.py.",
    )
    parser.add_argument(
        "--stream-comments",
        action="store_true",
        help="Stream each scraped comment line for live UI updates.",
    )
    args = parser.parse_args()

    if args.batch_size < 1:
        print("Error: --batch-size must be at least 1.", file=sys.stderr)
        return 2
    if not has_groq_key():
        print(
            "Error: GROQ_API_KEY was not found in the environment or project .env file. "
            "Add GROQ_API_KEY=your-key to .env, then run this command again.",
            file=sys.stderr,
        )
        return 2

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = PROJECT_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    shortcode = shortcode_from_url(args.url)
    raw_json = output_dir / f"{shortcode}_comments.json"
    cleaned_json = output_dir / f"{shortcode}_cleaned.json"
    moderated_json = output_dir / f"{shortcode}_moderated.json"
    bad_json = output_dir / f"{shortcode}_bad_comments.json"

    scrape_command = [
        PYTHON,
        str(BACKEND_DIR / "scrap.py"),
        args.url,
        "--output",
        str(raw_json),
    ]
    if args.show_browser:
        scrape_command.append("--show-browser")
    if args.stream_comments:
        scrape_command.append("--stream-comments")

    check_command = [
        PYTHON,
        str(BACKEND_DIR / "check.py"),
        str(cleaned_json),
        "--output",
        str(moderated_json),
        "--bad-output",
        str(bad_json),
        "--batch-size",
        str(args.batch_size),
    ]
    if args.model:
        check_command.extend(["--model", args.model])

    steps = [
        (
            "Scraping comments",
            scrape_command,
        ),
        (
            "Removing empty, emoji-only, and over-50-word comments",
            [PYTHON, str(BACKEND_DIR / "clear.py"), str(raw_json), "--output", str(cleaned_json)],
        ),
        (
            "Sending comments to Groq in batches",
            check_command,
        ),
    ]

    for index, (title, command) in enumerate(steps):
        result = run_step(title, command)
        if result:
            return result
        if index == 0:
            try:
                scraped = json.loads(raw_json.read_text(encoding="utf-8"))
                scraped_comments = scraped.get("comments", [])
            except (OSError, json.JSONDecodeError, AttributeError):
                scraped_comments = []
            if not scraped_comments:
                print(
                    "Stopping: Instagram returned no comments, so clear.py and Groq were not run. "
                    "Check that the post is public and has comments.",
                    file=sys.stderr,
                )
                return 1

    print(f"\nFinished. Bad comments with usernames: {bad_json}")
    print(f"Moderated JSON: {moderated_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
