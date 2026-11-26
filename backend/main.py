#!/usr/bin/env python3
"""Simple FastAPI backend that moderates comments from an Instagram post or Reel URL.

Run:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
or:
    python3 backend/main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pipeline


app = FastAPI(title="BKind Moderation API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Playwright's persistent browser profile (~/.bkind-playwright) cannot be shared
# concurrently by multiple browser processes, so serialize scraping runs.
_pipeline_lock = asyncio.Lock()


class ModerateRequest(BaseModel):
    url: str = Field(..., description="Public Instagram post, Reel, or TV URL")
    batch_size: int = Field(default=10, ge=1, le=50, description="Comments per Groq batch")
    model: str | None = Field(
        default=None,
        description="Optional Groq model ID override (leave empty or 'auto' to auto-select)",
    )
    show_browser: bool = Field(
        default=False,
        description="Force showing the browser window (otherwise only opens if login is needed)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://www.instagram.com/reel/POST_ID/",
                    "batch_size": 10,
                    "show_browser": False,
                }
            ]
        }
    }


class ModerateResponse(BaseModel):
    url: str
    total_scraped: int
    total_cleaned: int
    total_bad: int
    total_good: int
    scraped_comments: list[dict[str, Any]]
    good_comments: list[dict[str, Any]]
    bad_comments: list[dict[str, Any]]


def validate_instagram_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {
        "instagram.com",
        "www.instagram.com",
    }:
        raise ValueError("Please provide an Instagram post or Reel URL.")
    if not any(part in parsed.path for part in ("/p/", "/reel/", "/tv/")):
        raise ValueError("The URL should point to an Instagram post, Reel, or video.")
    return url.strip()


def build_pipeline_command(
    url: str,
    output_dir: Path,
    batch_size: int,
    model: str | None,
    show_browser: bool,
    stream_comments: bool = False,
) -> list[str]:
    command = [
        pipeline.PYTHON,
        "-u",
        str(BACKEND_DIR / "pipeline.py"),
        url,
        "--output-dir",
        str(output_dir),
        "--batch-size",
        str(batch_size),
    ]
    if model:
        command.extend(["--model", model])
    if show_browser:
        command.append("--show-browser")
    if stream_comments:
        command.append("--stream-comments")
    return command

# Jayesh's Code
async def run_pipeline_subprocess(
    url: str,
    output_dir: Path,
    batch_size: int,
    model: str | None,
    show_browser: bool,
) -> tuple[int, str]:
    command = build_pipeline_command(
        url=url,
        output_dir=output_dir,
        batch_size=batch_size,
        model=model,
        show_browser=show_browser,
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=pipeline.PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []
    assert process.stdout is not None
    while True:
        raw_line = await process.stdout.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if line:
            print(line, flush=True)
            lines.append(line)
    await process.wait()
    return process.returncode or 0, "\n".join(lines)


def normalize_model_name(model: str | None) -> str | None:
    requested = (model or "").strip()
    if requested.lower() in {"", "string", "auto", "null", "none"}:
        return None
    return requested


def extract_error_detail(logs: str) -> str:
    log_lines = [line.strip() for line in logs.splitlines() if line.strip()]
    error_lines = [
        line for line in log_lines
        if line.lower().startswith("error") or "failed" in line.lower()
    ]
    if error_lines:
        return " | ".join(error_lines[-3:])
    return log_lines[-1] if log_lines else "Moderation pipeline failed."


def build_moderate_response(
    clean_url: str,
    raw_json: Path,
    cleaned_json: Path,
    moderated_json: Path,
    bad_json: Path,
) -> ModerateResponse:
    raw_data = json.loads(raw_json.read_text(encoding="utf-8"))
    cleaned_data = json.loads(cleaned_json.read_text(encoding="utf-8"))
    moderated_data = json.loads(moderated_json.read_text(encoding="utf-8"))
    bad_data = json.loads(bad_json.read_text(encoding="utf-8"))

    scraped_comments = raw_data.get("comments", [])
    bad_comments = bad_data.get("comments", [])
    moderated_comments = moderated_data.get("comments", [])
    good_comments = [
        item
        for item in moderated_comments
        if item.get("moderation", {}).get("label") == "good"
    ]
    return ModerateResponse(
        url=bad_data.get("url", clean_url),
        total_scraped=len(scraped_comments),
        total_cleaned=len(cleaned_data.get("comments", [])),
        total_bad=len(bad_comments),
        total_good=len(good_comments),
        scraped_comments=scraped_comments,
        good_comments=good_comments,
        bad_comments=bad_comments,
    )


FRONTEND_DIR = pipeline.PROJECT_DIR / "frontend"


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/styles.css", include_in_schema=False)
async def serve_styles() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "styles.css",
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/app.js", include_in_schema=False)
async def serve_script() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "app.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/bg.jpg", include_in_schema=False)
async def serve_background() -> FileResponse:
    bg_path = FRONTEND_DIR / "bg.jpg"
    if not bg_path.is_file():
        bg_path = pipeline.PROJECT_DIR / "bg.jpg"
    return FileResponse(bg_path, media_type="image/jpeg")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/moderate", response_model=ModerateResponse)
async def moderate_post(payload: ModerateRequest) -> ModerateResponse:
    try:
        clean_url = validate_instagram_url(payload.url)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not pipeline.has_groq_key():
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY was not found in the environment or .env file.",
        )

    shortcode = pipeline.shortcode_from_url(clean_url)
    effective_model = normalize_model_name(payload.model)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        raw_json = output_dir / f"{shortcode}_comments.json"
        cleaned_json = output_dir / f"{shortcode}_cleaned.json"
        moderated_json = output_dir / f"{shortcode}_moderated.json"
        bad_json = output_dir / f"{shortcode}_bad_comments.json"

        async with _pipeline_lock:
            returncode, logs = await run_pipeline_subprocess(
                url=clean_url,
                output_dir=output_dir,
                batch_size=payload.batch_size,
                model=effective_model,
                show_browser=payload.show_browser,
            )

        if returncode != 0:
            raise HTTPException(status_code=502, detail=extract_error_detail(logs))

        try:
            return build_moderate_response(clean_url, raw_json, cleaned_json, moderated_json, bad_json)
        except (OSError, json.JSONDecodeError) as error:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read pipeline output files: {error}",
            ) from error


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_stream_response(
    url: str,
    batch_size: int = 10,
    model: str | None = None,
    show_browser: bool = False,
) -> StreamingResponse:
    try:
        clean_url = validate_instagram_url(url)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not pipeline.has_groq_key():
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY was not found in the environment or .env file.",
        )

    shortcode = pipeline.shortcode_from_url(clean_url)
    effective_model = normalize_model_name(model)

    async def event_generator():
        yield sse_event({
            "type": "stage",
            "stage": "scraping",
            "title": "Scraping comments",
            "detail": "Opening Instagram post and reading comments...",
        })

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            raw_json = output_dir / f"{shortcode}_comments.json"
            cleaned_json = output_dir / f"{shortcode}_cleaned.json"
            moderated_json = output_dir / f"{shortcode}_moderated.json"
            bad_json = output_dir / f"{shortcode}_bad_comments.json"

            command = build_pipeline_command(
                url=clean_url,
                output_dir=output_dir,
                batch_size=batch_size,
                model=effective_model,
                show_browser=show_browser,
                stream_comments=True,
            )
            log_lines: list[str] = []
            scraped_count = 0
            current_stage = "scraping"
            child_env = {**os.environ, "PYTHONUNBUFFERED": "1"}

            async with _pipeline_lock:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=pipeline.PROJECT_DIR,
                    env=child_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    assert process.stdout is not None
                    while True:
                        raw_line = await process.stdout.readline()
                        if not raw_line:
                            break
                        line = raw_line.decode("utf-8", errors="replace").rstrip()
                        if not line:
                            continue

                        if line.startswith("SCRAPED_COMMENT:"):
                            raw_item = line.split(":", 1)[1].strip()
                            try:
                                item = json.loads(raw_item)
                                scraped_count += 1
                                yield sse_event({
                                    "type": "comment",
                                    "count": scraped_count,
                                    "username": item.get("username", ""),
                                    "comment": item.get("comment", ""),
                                })
                            except json.JSONDecodeError:
                                pass
                            continue

                        print(line, flush=True)
                        log_lines.append(line)

                        if line.startswith("== Removing empty"):
                            current_stage = "cleaning"
                            yield sse_event({
                                "type": "stage",
                                "stage": current_stage,
                                "title": "Filtering noise",
                                "detail": "Removing empty and emoji-only comments...",
                            })
                        elif line.startswith("== Sending comments to Groq"):
                            current_stage = "moderating"
                            yield sse_event({
                                "type": "stage",
                                "stage": current_stage,
                                "title": "Checking comments with AI",
                                "detail": "Looking only for real abuse, slurs, and threats...",
                            })
                        elif "session missing or expired" in line.lower() or "requires sign-in" in line.lower():
                            current_stage = "login"
                            yield sse_event({
                                "type": "stage",
                                "stage": current_stage,
                                "title": "Waiting for Instagram sign-in",
                                "detail": "Complete sign-in in the opened browser window to continue...",
                            })
                        elif line.startswith("Reviewing comments ") or line.startswith("Groq ") or line.startswith("Approaching Groq") or line.startswith("Using Groq model:"):
                            yield sse_event({
                                "type": "stage",
                                "stage": current_stage,
                                "title": "Checking comments with AI",
                                "detail": line,
                            })

                    returncode = await process.wait()
                finally:
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()

            if returncode != 0:
                yield sse_event({"type": "error", "detail": extract_error_detail("\n".join(log_lines))})
                return

            try:
                result = build_moderate_response(clean_url, raw_json, cleaned_json, moderated_json, bad_json)
                yield sse_event({"type": "done", "result": result.model_dump()})
            except (OSError, json.JSONDecodeError) as error:
                yield sse_event({"type": "error", "detail": f"Failed to read pipeline output files: {error}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/moderate/stream")
async def moderate_post_stream_get(
    url: str,
    batch_size: int = 10,
    model: str | None = None,
    show_browser: bool = False,
) -> StreamingResponse:
    return build_stream_response(
        url=url,
        batch_size=batch_size,
        model=model,
        show_browser=show_browser,
    )


@app.post("/moderate/stream")
async def moderate_post_stream(payload: ModerateRequest) -> StreamingResponse:
    return build_stream_response(
        url=payload.url,
        batch_size=payload.batch_size,
        model=payload.model,
        show_browser=payload.show_browser,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
