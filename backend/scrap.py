#!/usr/bin/env python3
"""Collect visible comments on a public Instagram post or Reel.

Usage:
    python scrap.py "https://www.instagram.com/p/POST_ID/"

Playwright opens a normal browser window. Sign in manually if Instagram asks
you to; this script does not bypass login walls, private accounts, or other
access controls. The browser profile is saved in ~/.bkind-playwright so a
normal login can persist between runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright


MAX_COMMENTS = None
PROFILE_DIR = Path.home() / ".bkind-playwright"
STATE_FILE = PROFILE_DIR / "storage_state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect visible comments from an Instagram post or Reel."
    )
    parser.add_argument("url", help="URL of a public Instagram post or Reel")
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument(
        "--headless",
        dest="browser_mode",
        action="store_const",
        const="headless",
        help="Always keep the browser hidden and fail if Instagram requires login.",
    )
    browser_group.add_argument(
        "--show-browser",
        dest="browser_mode",
        action="store_const",
        const="show",
        help="Always show the browser window.",
    )
    parser.set_defaults(browser_mode="auto")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for saving the JSON result.",
    )
    parser.add_argument(
        "--stream-comments",
        action="store_true",
        help="Emit each newly scraped comment as a JSON line on stdout.",
    )
    return parser.parse_args()


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {
        "instagram.com",
        "www.instagram.com",
    }:
        raise ValueError("Please provide an Instagram post or Reel URL.")
    if not any(part in parsed.path for part in ("/p/", "/reel/", "/tv/")):
        raise ValueError("The URL should point to an Instagram post, Reel, or video.")
    return url


async def read_comments(page, limit: int | None) -> dict:
    """Find individual comment rows, avoiding larger post/comment containers."""
    return await page.evaluate(
        """(limit) => {
          const ignored = /^(like|reply|see translation|hide replies|view(?: all)?(?: \\d+)? replies?|\\d+ likes?|\\d+\\s*[smhdwy]|\\d+\\s*(minutes?|hours?|days?|weeks?|years?)\\s*ago|follow|original audio|·)$/i;
          const replyAction = /^(reply|view(?: all)?(?: \\d+)? replies?|hide replies)$/i;
          const found = [];
          const seen = new Set();
          const profileLinks = [...document.querySelectorAll('a[href]')].filter(a => {
            const path = new URL(a.href, location.href).pathname;
            return /^\\/[A-Za-z0-9._]+\\/?$/.test(path);
          });

          for (const userLink of profileLinks) {
            const username = userLink.innerText.trim().replace(/^@/, '');
            if (!username || username.toLowerCase() === 'instagram') continue;
            const rows = [];
            let node = userLink;
            for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
              rows.push(node);
            }

            // Pick the smallest nearby container that has a Reply control and where
            // userLink is the comment author (first profile link in the row), not an @mention.
            let best = null;
            for (const row of rows) {
              const text = (row.innerText || '').trim();
              if (!text || text.length > 1800) continue;
              const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
              if (lines.length < 2 || !lines.some(s => replyAction.test(s))) continue;
              const rowLinks = [...row.querySelectorAll('a[href]')].filter(a =>
                /^\\/[A-Za-z0-9._]+\\/?$/.test(new URL(a.href, location.href).pathname)
              );
              const firstAuthorLink = rowLinks.find(a => a.innerText.trim());
              if (firstAuthorLink && firstAuthorLink !== userLink) continue;
              const rowUsernames = [...new Set(
                rowLinks
                  .map(a => a.innerText.trim().replace(/^@/, '').toLowerCase())
                  .filter(Boolean)
              )];
              // A comment can contain mentions, but a container with many
              // distinct profiles is almost certainly the whole comment list.
              if (rowUsernames.length > 6) continue;
              const ownName = username.toLowerCase();
              const body = lines.filter(s =>
                s.replace(/^@/, '').toLowerCase() !== ownName && !ignored.test(s)
              ).join(' ').replace(/^\\d+\\s*[smhdwy]\\s*(?:·\\s*Edited\\s*)?/i, '').trim();
              if (!body || body.length > 1500) continue;
              if (!best || text.length < best.textLength) {
                best = {body, textLength: text.length};
              }
            }
            const comment = best?.body || '';
            if (!comment) continue;
            const key = username.toLowerCase() + '\\0' + comment;
            if (seen.has(key)) continue;
            seen.add(key);
            found.push({username, comment});
            if (limit !== null && found.length >= limit) break;
          }
          return {
            comments: found,
            profileLinks: profileLinks.length,
            listItems: document.querySelectorAll('li').length
          };
        }""",
        limit,
    )


async def open_comments(page) -> bool:
    """Try accessible controls and SVG labels to open the comments panel."""
    try:
        clicked = await page.evaluate(
            """() => {
              const candidates = [...document.querySelectorAll('button, [role="button"], a')]
                .map(el => {
                  const svgLabels = [...el.querySelectorAll('svg[aria-label]')]
                    .map(svg => svg.getAttribute('aria-label') || '');
                  const label = [el.getAttribute('aria-label') || '', el.title || '',
                    el.innerText || '', ...svgLabels].join(' ');
                  return {el, label};
                })
                .filter(item => /\\bcomments?\\b/i.test(item.label))
                .sort((a, b) => a.label.length - b.label.length);
              for (const item of candidates) {
                const rect = item.el.getBoundingClientRect();
                const style = getComputedStyle(item.el);
                if (rect.width && rect.height && style.visibility !== 'hidden' &&
                    style.display !== 'none') {
                  item.el.click();
                  return true;
                }
              }
              return false;
            }"""
        )
        if clicked:
            await page.wait_for_timeout(1200)
            return True

        # Fallback to Playwright's accessible-name matching.
        button = page.get_by_role("button", name=re.compile(r"comments?", re.I)).first
        if await button.is_visible(timeout=1000):
            await button.click(timeout=2000)
            await page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


async def scroll_comments(page, nudge_up: bool = False) -> bool:
    """Click load-more/hidden comment controls, scroll the comment list, and report if a loader is visible."""
    # Only expand top-level / hidden comments; leave nested reply threads collapsed.
    try:
        clicked_more = await page.evaluate(
            """() => {
              const loadMorePattern = /\\b(?:load|view|show|see)\\b.*\\b(?:more|hidden)\\b.*\\bcomments?\\b|^more comments?$|^load more comments$/i;
              const controls = [...document.querySelectorAll('button, [role="button"], a')];
              for (const el of controls) {
                const svgLabels = [...el.querySelectorAll('svg[aria-label]')]
                  .map(svg => svg.getAttribute('aria-label') || '');
                const label = [
                  el.innerText || '',
                  el.getAttribute('aria-label') || '',
                  el.title || '',
                  ...svgLabels,
                ].join(' ').replace(/\\s+/g, ' ').trim();
                if (!loadMorePattern.test(label)) continue;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none') {
                  el.scrollIntoView({block: 'center'});
                  el.click();
                  return true;
                }
              }
              return false;
            }"""
        )
        if clicked_more:
            await page.wait_for_timeout(900)
    except Exception:
        pass

    # Fallback text locator for non-button "Load/View more/hidden comments" controls.
    try:
        more_control = page.get_by_text(
            re.compile(r"\b(?:view|show|load|see)\b.*\b(?:more|hidden)\b.*\bcomments?\b", re.I)
        ).first
        if await more_control.is_visible(timeout=250):
            await more_control.scroll_into_view_if_needed(timeout=1000)
            await more_control.click(timeout=1000)
            await page.wait_for_timeout(800)
    except Exception:
        pass

    if nudge_up:
        await page.evaluate(
            """() => {
              const profilePath = /^\\/[A-Za-z0-9._]+\\/?$/;
              const divs = [...document.querySelectorAll('*')].filter(el =>
                el !== document.documentElement && el !== document.body &&
                /(auto|scroll|overlay)/i.test(getComputedStyle(el).overflowY) &&
                el.scrollHeight > el.clientHeight + 20 &&
                [...el.querySelectorAll('a[href]')].some(a =>
                  profilePath.test(new URL(a.href, location.href).pathname)
                )
              );
              const last = divs[divs.length - 1];
              if (last && last.scrollTop > 200) {
                last.scrollTop = Math.max(0, last.scrollTop - 250);
              } else {
                window.scrollBy(0, -250);
              }
            }"""
        )
        await page.wait_for_timeout(250)

    # Pick the innermost genuinely scrollable container that holds the comment list.
    # Outer wrappers contain all the same profile links plus extra page links, so we
    # exclude ancestors whose descendant scrollable element already holds comments.
    scroll_info = await page.evaluate(
        """() => {
          const profilePath = /^\\/[A-Za-z0-9._]+\\/?$/;
          const replyWord = /\\b(?:reply|replies)\\b/i;

          const allScrollable = [...document.querySelectorAll('*')].filter(el => {
            if (el === document.documentElement || el === document.body) return false;
            const style = getComputedStyle(el);
            if (!/(auto|scroll|overlay)/i.test(style.overflowY)) return false;
            if (el.scrollHeight <= el.clientHeight + 20) return false;
            const rect = el.getBoundingClientRect();
            return rect.height >= 120 && rect.width >= 150 &&
              style.visibility !== 'hidden' && style.display !== 'none';
          }).map(el => {
            const links = [...el.querySelectorAll('a[href]')].filter(a =>
              profilePath.test(new URL(a.href, location.href).pathname)
            ).length;
            const replyControls = [...el.querySelectorAll('button, [role="button"], span, a')]
              .filter(node => replyWord.test((node.innerText || '').trim())).length;
            return {el, links, replyControls, height: el.clientHeight};
          }).filter(item => item.links > 0);

          // Drop outer wrappers that contain an inner scrollable comment pane.
          const leafCandidates = allScrollable.filter(outer =>
            !allScrollable.some(inner =>
              inner.el !== outer.el &&
              outer.el.contains(inner.el) &&
              (inner.replyControls >= Math.max(1, Math.floor(outer.replyControls * 0.5)) ||
               inner.links >= Math.max(2, Math.floor(outer.links * 0.5)))
            )
          );

          const pool = leafCandidates.length ? leafCandidates : allScrollable;
          pool.sort((a, b) =>
            (b.replyControls * 25 + b.links * 10) - (a.replyControls * 25 + a.links * 10) ||
            a.height - b.height
          );

          const target = pool[0]?.el;
          if (target) {
            const step = Math.max(target.clientHeight * 0.85, 650);
            const nextTop = Math.min(target.scrollHeight, target.scrollTop + step);
            target.scrollTop = nextTop;
            // Also ensure the bottom of the container is reached to trigger IntersectionObserver.
            if (target.scrollHeight - (target.scrollTop + target.clientHeight) < step) {
              target.scrollTop = target.scrollHeight;
            }
            target.dispatchEvent(new Event('scroll', {bubbles: true}));
          } else {
            window.scrollBy(0, Math.max(window.innerHeight * 0.85, 650));
          }

          const loading = Boolean(
            document.querySelector('[role="progressbar"], svg[aria-label*="Loading" i]')
          );
          return {usedContainer: Boolean(target), loading};
        }"""
    )
    await page.wait_for_timeout(1600 if scroll_info.get("loading") else 1000)
    return bool(scroll_info.get("loading"))


def merge_comments(
    all_comments: list[dict[str, str]],
    seen_keys: set[tuple[str, str]],
    scanned: list[dict[str, str]],
    limit: int | None,
    stream_comments: bool = False,
) -> int:
    """Append unseen comments in encounter order and return how many were added."""
    added = 0
    for item in scanned:
        if limit is not None and len(all_comments) >= limit:
            break
        key = (item["username"].lower(), item["comment"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        all_comments.append(item)
        added += 1
        if stream_comments:
            print(f"SCRAPED_COMMENT: {json.dumps(item, ensure_ascii=False)}", flush=True)
    return added


async def get_session_id(context) -> str | None:
    """Return the active Instagram sessionid cookie value, if present."""
    try:
        cookies = await context.cookies("https://www.instagram.com")
        for cookie in cookies:
            if cookie.get("name") == "sessionid" and cookie.get("value"):
                return str(cookie["value"])
    except Exception:
        pass
    return None


async def is_login_required(page, context) -> bool:
    """Detect when Instagram is showing a login wall, challenge, or logged-out page."""
    if any(part in page.url for part in ("/accounts/login", "/accounts/suspended", "/challenge")):
        return True
    has_sid = bool(await get_session_id(context))
    logged_out_ui = await page.evaluate(
        """() => {
          if (document.querySelector('input[name="password"]')) return true;
          const loginControls = [...document.querySelectorAll('a, button, [role="button"]')];
          return loginControls.some(el => {
            const text = (el.innerText || '').trim();
            if (!/^log in$/i.test(text)) return false;
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 &&
              style.visibility !== 'hidden' && style.display !== 'none';
          });
        }"""
    )
    return bool(logged_out_ui or not has_sid)


async def wait_for_manual_login(context, page, timeout_seconds: int = 300) -> bool:
    """Wait until the user finishes signing in inside the visible browser."""
    old_sid = await get_session_id(context)
    was_login_form = await page.evaluate(
        """() => Boolean(document.querySelector('input[name="password"]')) ||
                  location.pathname.includes('/accounts/login')"""
    )
    for _ in range(timeout_seconds):
        await asyncio.sleep(1.0)
        try:
            current_sid = await get_session_id(context)
            on_login_page = await page.evaluate(
                """() => Boolean(document.querySelector('input[name="password"]')) ||
                          location.pathname.includes('/accounts/login') ||
                          location.pathname.includes('/challenge')"""
            )
            if not on_login_page and current_sid:
                if old_sid is None or current_sid != old_sid or was_login_form:
                    return True
        except Exception:
            # Page may be mid-navigation while logging in.
            continue
    return False


async def save_storage_state(context) -> None:
    """Persist cookies and localStorage to disk so headless runs reuse headed logins."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(STATE_FILE))
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


async def scrape_once(
    playwright,
    url: str,
    headless: bool,
    require_login: bool = False,
    stream_comments: bool = False,
) -> dict:
    """Run one scrape attempt in either headless or visible mode."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    browser = await playwright.chromium.launch(headless=headless)
    context_kwargs: dict = {"viewport": {"width": 1280, "height": 900}}
    if STATE_FILE.is_file():
        context_kwargs["storage_state"] = str(STATE_FILE)
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1800)

        login_wall = any(
            part in page.url for part in ("/accounts/login", "/accounts/suspended", "/challenge")
        ) or await page.evaluate(
            """() => Boolean(document.querySelector('input[name="password"]'))"""
        )
        if login_wall or await is_login_required(page, context):
            if headless:
                return {"status": "needs_login", "comments": [], "final_url": page.url, "scan": {}}
            print(
                "Instagram requires sign-in. Complete login in the browser; "
                "the script will continue automatically (waiting up to 5 minutes).",
                flush=True,
            )
            if not await wait_for_manual_login(context, page):
                return {"status": "timeout", "comments": [], "final_url": page.url, "scan": {}}
            await save_storage_state(context)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1800)

        print("Reading available comments...", flush=True)
        all_comments: list[dict[str, str]] = []
        seen_keys: set[tuple[str, str]] = set()
        scan = await read_comments(page, MAX_COMMENTS)
        merge_comments(
            all_comments, seen_keys, scan["comments"], MAX_COMMENTS, stream_comments=stream_comments
        )
        if not all_comments:
            await open_comments(page)
            scan = await read_comments(page, MAX_COMMENTS)
            merge_comments(
                all_comments, seen_keys, scan["comments"], MAX_COMMENTS, stream_comments=stream_comments
            )
        if all_comments:
            print(f"Collected {len(all_comments)} comments so far...", flush=True)

        stale_scrolls = 0
        while stale_scrolls < 15:
            if MAX_COMMENTS is not None and len(all_comments) >= MAX_COMMENTS:
                break
            nudge_up = stale_scrolls > 0 and stale_scrolls % 3 == 0
            still_loading = await scroll_comments(page, nudge_up=nudge_up)
            scan = await read_comments(page, MAX_COMMENTS)
            added = merge_comments(
                all_comments, seen_keys, scan["comments"], MAX_COMMENTS, stream_comments=stream_comments
            )

            if added == 0:
                if still_loading and stale_scrolls < 12:
                    await page.wait_for_timeout(1200)
                    scan = await read_comments(page, MAX_COMMENTS)
                    added = merge_comments(
                        all_comments, seen_keys, scan["comments"], MAX_COMMENTS, stream_comments=stream_comments
                    )
                if added == 0:
                    stale_scrolls += 1
                else:
                    stale_scrolls = 0
                    print(f"Collected {len(all_comments)} comments so far...", flush=True)
            else:
                stale_scrolls = 0
                print(f"Collected {len(all_comments)} comments so far...", flush=True)

        if await get_session_id(context):
            await save_storage_state(context)

        return {
            "status": "ok",
            "comments": all_comments,
            "final_url": page.url,
            "scan": scan,
        }
    finally:
        await context.close()
        await browser.close()


async def main() -> int:
    args = parse_args()
    try:
        url = validate_url(args.url)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    async with async_playwright() as playwright:
        if args.browser_mode == "show":
            outcome = await scrape_once(
                playwright, url, headless=False, stream_comments=args.stream_comments
            )
        else:
            outcome = await scrape_once(
                playwright, url, headless=True, stream_comments=args.stream_comments
            )
            if outcome["status"] == "needs_login":
                if args.browser_mode == "headless":
                    print(
                        "Instagram requires sign-in. Run without --headless (or with --show-browser) "
                        "to sign in through the browser window.",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    "Instagram session missing or expired. Opening browser window for sign-in...",
                    flush=True,
                )
                outcome = await scrape_once(
                    playwright,
                    url,
                    headless=False,
                    require_login=True,
                    stream_comments=args.stream_comments,
                )

        if outcome["status"] == "timeout":
            print("Login was not completed before the 5-minute timeout.", file=sys.stderr)
            return 1

        comments = outcome["comments"]
        scan = outcome["scan"]
        final_url = outcome["final_url"]
        result = {"url": final_url, "comments": comments}
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(serialized + "\n", encoding="utf-8")
            print(f"Saved {len(comments)} comments to {args.output}")
        else:
            print(serialized)
        if not comments:
            print(
                "No comments were detected. The post may be private, unavailable, "
                "or Instagram may have changed its page layout. "
                f"Current page: {final_url}. "
                f"Visible profile links: {scan.get('profileLinks', 0)}; "
                f"list items: {scan.get('listItems', 0)}.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
