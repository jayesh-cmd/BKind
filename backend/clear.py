#!/usr/bin/env python3
"""Filter scraped Instagram comments and save a cleaned JSON file.

Usage:
    python3 clear.py comments.json
    python3 clear.py comments.json --output comments_cleaned.json

Comments are removed when they are blank, contain only emoji characters, or
contain more than 50 words. The input file is left unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_WORDS = 50
WORD_RE = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)
EMOJI_EXTRA = {
    0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
    0x3030, 0x303D, 0x3297, 0x3299,
}
IGNORABLE_EMOJI_PARTS = {
    0x200D,  # zero-width joiner, used in family/profession emoji
    0xFE0E,  # text presentation selector
    0xFE0F,  # emoji presentation selector
    0x20E3,  # combining keycap
}


def is_emoji_codepoint(codepoint: int) -> bool:
    """Recognize common Unicode emoji and emoji-capable symbol ranges."""
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
        or 0x2B00 <= codepoint <= 0x2BFF
        or codepoint in EMOJI_EXTRA
    )


def is_emoji_only(text: str) -> bool:
    """Return True when every non-space character is part of an emoji sequence."""
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return False

    has_keycap = "\u20e3" in chars
    for char in chars:
        codepoint = ord(char)
        if (
            is_emoji_codepoint(codepoint)
            or codepoint in IGNORABLE_EMOJI_PARTS
            or 0xE0020 <= codepoint <= 0xE007F  # emoji flag tag sequence
        ):
            continue
        # Keycap emoji sequences can start with a digit, #, or *.
        if has_keycap and char in "0123456789#*":
            continue
        return False
    return True


def clean_comments(data: Any) -> tuple[Any, dict[str, int]]:
    """Filter the comments array while preserving the input JSON structure."""
    if isinstance(data, dict):
        comments = data.get("comments")
        if not isinstance(comments, list):
            raise ValueError('Expected a JSON object with a "comments" list.')
    elif isinstance(data, list):
        comments = data
    else:
        raise ValueError("Expected a JSON object or a JSON list of comments.")

    kept = []
    removed = {"empty": 0, "emoji_only": 0, "over_50_words": 0}
    for item in comments:
        comment_text = str(item.get("comment", "")) if isinstance(item, dict) else str(item)
        comment_text = comment_text.strip()

        if not comment_text:
            removed["empty"] += 1
        elif is_emoji_only(comment_text):
            removed["emoji_only"] += 1
        elif len(WORD_RE.findall(comment_text)) > MAX_WORDS:
            removed["over_50_words"] += 1
        else:
            if isinstance(item, dict):
                item = {**item, "comment": comment_text}
            kept.append(item)

    cleaned = {**data, "comments": kept} if isinstance(data, dict) else kept
    return cleaned, removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove blank, emoji-only, and over-50-word comments from scraped JSON."
    )
    parser.add_argument("input", type=Path, help="JSON file produced by scrap.py")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: INPUT_cleaned.json).",
    )
    args = parser.parse_args()

    output_path = args.output or args.input.with_name(
        f"{args.input.stem}_cleaned{args.input.suffix or '.json'}"
    )

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        cleaned, removed = clean_comments(data)
        output_path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    kept_count = len(cleaned["comments"] if isinstance(cleaned, dict) else cleaned)
    removed_count = sum(removed.values())
    print(f"Saved {kept_count} comments to {output_path}")
    print(
        "Removed: "
        f"{removed['empty']} empty, "
        f"{removed['emoji_only']} emoji-only, "
        f"{removed['over_50_words']} over 50 words "
        f"({removed_count} total)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
