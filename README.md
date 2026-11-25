# BKind

**Making the Internet Peaceful Again.**  
BKind is a lightweight Instagram comment moderation tool. Paste any public Instagram post or Reel URL into the web app (or CLI), and BKind scrapes the main comments, filters out noise, and uses Groq AI to surface **only abuse, slurs, sexual harassment, or serious threats** — leaving sarcasm, friendly banter, jokes, and promotions untouched.

---

## Project Structure

```text
BKind/
├── backend/
│   ├── main.py         # FastAPI server, SSE live stream, static frontend routes
│   ├── pipeline.py     # Orchestrates scrap.py -> clear.py -> check.py
│   ├── scrap.py        # Playwright Instagram comment scraper + session persistence
│   ├── clear.py        # Filters empty, emoji-only, and >50-word comments
│   └── check.py        # Batched Groq AI moderation with rate-limit handling
├── frontend/
│   ├── index.html      # Single-page UI
│   ├── styles.css      # Glassmorphism + pixel typography + responsive layout
│   ├── app.js          # SSE live comment ticker, category tabs, .txt downloads
│   └── bg.jpg          # Background artwork
├── .env                # Stores GROQ_API_KEY (not committed)
├── requirements.txt    # Python dependencies
└── venv/               # Local Python virtual environment
```

---

## How the Project Works (Under the Hood)

Every scan flows through a 3-stage pipeline managed by [`backend/pipeline.py`](backend/pipeline.py) and exposed to the browser over Server-Sent Events (SSE) by [`backend/main.py`](backend/main.py):

```text
Instagram URL ──▶ scrap.py ──▶ clear.py ──▶ check.py ──▶ Web UI / .txt Export
                   (raw)       (cleaned)    (moderated)
```

### 1. Scraping Comments (`backend/scrap.py`)
* **Auto-Headless Browser & Saved Session:** Uses Playwright Chromium in hidden (`headless=True`) mode by default. Cookies and `localStorage` are saved in `~/.bkind-playwright/storage_state.json` (`0600` permissions) so both visible and hidden runs share the same session on macOS. If the session is missing or expired (e.g., Instagram shows a login prompt or missing `sessionid` cookie), `scrap.py` automatically opens a visible browser window for sign-in, saves the refreshed session, and stays hidden on future runs.
* **Extracting Main Comments Only:** Walks the DOM from profile links (`a[href]` matching `/username/`), verifies the link is the **comment author** (first profile link in the row, preventing `@mentions` inside comment bodies from creating duplicate rows), strips UI labels (`Like`, `Reply`, timestamps like `2h`, `20 h · Edited`), and keeps only top-level/hidden comments (nested reply threads stay collapsed).
* **Smart Scrolling & Accumulation:** Identifies the innermost scrollable comments container (ignoring outer page/modal wrappers), clicks `"Load/View more/hidden comments"` controls, waits for Instagram loading spinners, and accumulates unique `(username, comment)` pairs across scroll passes so virtualized DOM unmounting never loses earlier comments.
* **Live Streaming:** With `--stream-comments`, prints each newly discovered comment as `SCRAPED_COMMENT: {...}` immediately so the frontend can animate comments live as they arrive.

### 2. Cleaning Comments (`backend/clear.py`)
Before calling the AI, `clear.py` removes comments that don't need LLM review (saving tokens and speed):
* **Empty / whitespace-only comments**
* **Emoji-only comments** (handles standard emoji blocks, variation selectors, ZWJ family/profession sequences, regional flag tags, and keycap emojis)
* **Over-50-word comments** (checked via Unicode word regex)

The remaining comments are kept in their original structure for AI classification.

### 3. AI Moderation (`backend/check.py`)
* **Automatic Model Discovery (`resolve_model`):** Queries `client.models.list()` on your Groq API key, skips non-chat models (Whisper, TTS, prompt-guard), and selects the best available chat model (or uses `--model` if specified).
* **Small Batches & Incremental Saves:** Sends cleaned comments in batches of **10** (configurable via `--batch-size`) with a strict moderation policy.
* **High-Precision 1–5 Severity Threshold (`MIN_BAD_SEVERITY = 4`):**
  * **1–3 (`good`):** Compliments, sarcasm, jokes, memes, promotions/links, ordinary criticism, and playful English/Hinglish friend banter.
  * **4–5 (`bad`):** Only clear verbal abuse, targeted profanity/slurs/gaali, hate speech, explicit sexual harassment, or serious threats of real physical violence.
  * Any comment rated below severity `4` is automatically treated as `good`, and categories are restricted to abuse/threat labels (`insult`, `bullying`, `harassment`, `threat`, `hate_or_slur`, `discrimination`, `sexual_harassment`).
* **Rate-Limit & Error Resilience:**
  * **Proactive throttling:** Inspects Groq's `x-ratelimit-remaining-tokens` / `x-ratelimit-remaining-requests` headers after each batch and pauses until bucket reset if remaining capacity gets low.
  * **Automatic retries:** Handles `429` rate limits (parsing `retry-after` / `"try again in Xs"`) and transient connection/server errors.
  * **Automatic batch splitting:** If a batch exceeds the per-request token cap (`413`) or fails strict JSON validation (`400 json_validate_failed`), it splits the batch in half recursively (`10 -> 5 + 5`, etc.) and falls back to plain JSON extraction (`extract_json_object`) if needed.
  * **Safe failure handling:** Partial progress is saved after every batch; unreviewed comments from a failed run are marked in `*_moderated.json` without polluting `*_bad_comments.json`.

### 4. Backend API & Frontend UI (`backend/main.py` & `frontend/`)
* **Zero Local Clutter (`tempfile.TemporaryDirectory`):** Web requests run `pipeline.py` inside a temporary directory that is automatically deleted once results are returned. An `asyncio.Lock` serializes runs so concurrent requests don't collide on the Playwright session.
* **Live SSE Streaming (`GET /moderate/stream`):** Streams real-time stage updates (`scraping`, `login`, `cleaning`, `moderating`) and live `@username: comment` ticker events to the browser using native `EventSource`, while `POST /moderate` provides a standard JSON endpoint.
* **Glassmorphism Single-Page UI:** Built with vanilla HTML/CSS/JS using [bg.jpg](frontend/bg.jpg), `Pixelify Sans` + `IBM Plex Mono` fonts, responsive mobile/tablet/desktop layout, a live rolling status stream, a compact scrollable comments box, clickable **Comments Scraped** / **Good** / **Flagged** tabs (defaulting to **Flagged**), and per-category `.txt` file downloads formatted with blank-line spacing (`@username: comment`).

---

## Setup & Running

### 1. Install dependencies
```zsh
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium
```

### 2. Configure your Groq API key
Create a `.env` file in the project root:
```env
GROQ_API_KEY="your-groq-api-key"
```

### 3. Start the web app
```zsh
./venv/bin/python backend/main.py
```
Then open `http://localhost:8000` in your browser (API docs are at `http://localhost:8000/docs`).

### Optional: Run from the command line
```zsh
./venv/bin/python backend/pipeline.py "https://www.instagram.com/reel/POST_ID/"
```
CLI runs save intermediate and final JSON files into `results/`.
