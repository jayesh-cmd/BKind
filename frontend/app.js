const IS_LOCAL_HOST =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";
const API_BASE =
  window.location.protocol === "file:" ||
  (IS_LOCAL_HOST && window.location.port !== "8000")
    ? "http://127.0.0.1:8000"
    : "";

const form = document.getElementById("moderate-form");
const urlInput = document.getElementById("post-url");
const submitBtn = document.getElementById("submit-btn");
const btnSpinner = document.getElementById("btn-spinner");
const btnLabel = document.getElementById("btn-label");
const liveActivity = document.getElementById("live-activity");
const liveTitle = document.getElementById("live-title");
const liveCount = document.getElementById("live-count");
const liveDetail = document.getElementById("live-detail");
const statusMessage = document.getElementById("status-message");
const resultsSection = document.getElementById("results-section");
const emptyState = document.getElementById("empty-state");
const emptyTitle = document.getElementById("empty-title");
const emptyDesc = document.getElementById("empty-desc");
const commentsBox = document.getElementById("comments-box");
const badList = document.getElementById("bad-list");

const statScraped = document.getElementById("stat-scraped");
const statGood = document.getElementById("stat-good");
const statBad = document.getElementById("stat-bad");
const tabButtons = document.querySelectorAll(".tab-btn");
const downloadButtons = document.querySelectorAll(".download-btn");

let commentQueue = [];
let tickerTimer = null;
let activeEventSource = null;
let pendingStageDetail = "";
let latestResult = null;
let activeTab = "flagged";

function setError(text) {
  if (!text) {
    statusMessage.textContent = "";
    statusMessage.classList.add("hidden");
    statusMessage.classList.remove("error");
    return;
  }
  statusMessage.textContent = text;
  statusMessage.classList.remove("hidden");
  statusMessage.classList.add("error");
}

function animateDetail(text) {
  liveDetail.textContent = text;
  liveDetail.classList.remove("flip");
  void liveDetail.offsetWidth;
  liveDetail.classList.add("flip");
}

function stopStream() {
  if (tickerTimer !== null) {
    clearInterval(tickerTimer);
    tickerTimer = null;
  }
  if (activeEventSource !== null) {
    activeEventSource.close();
    activeEventSource = null;
  }
  commentQueue = [];
  pendingStageDetail = "";
}

function startTicker() {
  if (tickerTimer !== null) {
    clearInterval(tickerTimer);
  }
  tickerTimer = setInterval(() => {
    if (commentQueue.length > 0) {
      if (commentQueue.length > 24) {
        commentQueue.splice(0, commentQueue.length - 14);
      }
      const next = commentQueue.shift();
      if (next) {
        animateDetail(`@${next.username}: ${next.comment}`);
      }
      return;
    }
    if (pendingStageDetail) {
      animateDetail(pendingStageDetail);
      pendingStageDetail = "";
    }
  }, 135);
}

function showLiveActivity(title, detail, count = null) {
  liveActivity.classList.remove("hidden");
  if (title) liveTitle.textContent = title;
  if (detail) {
    if (commentQueue.length > 0) {
      pendingStageDetail = detail;
    } else {
      animateDetail(detail);
    }
  }
  if (count !== null && count > 0) {
    liveCount.textContent = `${count} collected`;
    liveCount.classList.remove("hidden");
  }
}

function hideLiveActivity() {
  stopStream();
  liveActivity.classList.add("hidden");
  liveCount.classList.add("hidden");
  liveCount.textContent = "";
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  urlInput.disabled = isLoading;
  btnSpinner.classList.toggle("hidden", !isLoading);
  btnLabel.textContent = isLoading ? "Scanning..." : "Scan Comments";
}

function getListForTab(tab) {
  if (!latestResult) return [];
  if (tab === "scraped") {
    return Array.isArray(latestResult.scraped_comments) ? latestResult.scraped_comments : [];
  }
  if (tab === "good") {
    return Array.isArray(latestResult.good_comments) ? latestResult.good_comments : [];
  }
  return Array.isArray(latestResult.bad_comments) ? latestResult.bad_comments : [];
}

function updateActiveTabUI() {
  for (const btn of tabButtons) {
    const isSelected = btn.dataset.tab === activeTab;
    btn.setAttribute("aria-selected", isSelected ? "true" : "false");
    const group = btn.closest(".stat-group");
    if (group) {
      group.classList.toggle("active", isSelected);
    }
  }
}

function renderActiveTabList() {
  updateActiveTabUI();
  if (commentsBox) commentsBox.scrollTop = 0;
  badList.replaceChildren();
  const items = getListForTab(activeTab);

  if (items.length === 0) {
    if (activeTab === "flagged") {
      emptyTitle.textContent = "A calm thread.";
      emptyDesc.textContent = "No abusive or threatening comments were found on this post.";
    } else if (activeTab === "good") {
      emptyTitle.textContent = "No good comments.";
      emptyDesc.textContent = "No non-abusive text comments remained after cleaning.";
    } else {
      emptyTitle.textContent = "No comments scraped.";
      emptyDesc.textContent = "No visible comments were collected from this post.";
    }
    emptyState.classList.remove("hidden");
    return;
  }

  emptyState.classList.add("hidden");
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "glass-card";

    const head = document.createElement("div");
    head.className = "comment-head";

    const username = document.createElement("span");
    username.className = "comment-user";
    username.textContent = `@${item.username || "unknown"}`;
    head.appendChild(username);

    const moderation = item.moderation;
    if (moderation) {
      const tagWrap = document.createElement("div");
      tagWrap.className = "category-tags";
      const categories = Array.isArray(moderation.categories) ? moderation.categories : [];
      if (moderation.label === "good") {
        const tag = document.createElement("span");
        tag.className = "category-tag good-tag";
        tag.textContent = "good";
        tagWrap.appendChild(tag);
      } else {
        for (const cat of categories) {
          const tag = document.createElement("span");
          tag.className = "category-tag";
          tag.textContent = cat.replace(/_/g, " ");
          tagWrap.appendChild(tag);
        }
      }
      if (tagWrap.childNodes.length > 0) {
        head.appendChild(tagWrap);
      }
    }

    const commentBody = document.createElement("p");
    commentBody.className = "comment-text";
    commentBody.textContent = item.comment || "";
    card.append(head, commentBody);

    if (moderation?.reason) {
      const reason = document.createElement("p");
      reason.className = "comment-reason";
      reason.textContent = moderation.reason;
      card.appendChild(reason);
    }

    badList.appendChild(card);
  }
}

function downloadComments(tab) {
  if (!latestResult) return;
  const items = getListForTab(tab);
  const labelMap = {
    scraped: "scraped_comments",
    good: "good_comments",
    flagged: "flagged_comments",
  };
  let shortcode = "instagram";
  try {
    const parts = new URL(latestResult.url).pathname.split("/").filter(Boolean);
    if (parts.length >= 2) shortcode = parts[1].replace(/[^A-Za-z0-9_-]/g, "_");
  } catch {
    // Keep fallback filename
  }

  const lines = items.map((item) => {
    const username = item.username || "unknown";
    const comment = String(item.comment || "").replace(/\s+/g, " ").trim();
    return `@${username}: ${comment}`;
  });
  const textContent = lines.join("\n\n") + (lines.length ? "\n" : "");
  const blob = new Blob([textContent], {
    type: "text/plain;charset=utf-8",
  });
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `${shortcode}_${labelMap[tab] || "comments"}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(blobUrl);
}

function renderResults(data) {
  latestResult = data;
  statScraped.textContent = String(data.total_scraped ?? 0);
  statGood.textContent = String(data.total_good ?? 0);
  statBad.textContent = String(data.total_bad ?? 0);

  for (const dlBtn of downloadButtons) {
    const count = getListForTab(dlBtn.dataset.download).length;
    dlBtn.disabled = count === 0;
  }

  activeTab = "flagged";
  renderActiveTabList();
  resultsSection.classList.remove("hidden");
}

for (const btn of tabButtons) {
  btn.addEventListener("click", () => {
    if (!latestResult) return;
    activeTab = btn.dataset.tab || "flagged";
    renderActiveTabList();
  });
}

for (const dlBtn of downloadButtons) {
  dlBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    downloadComments(dlBtn.dataset.download || "flagged");
  });
}

function handleStreamEvent(event) {
  if (!event || !event.type) return;

  if (event.type === "stage") {
    showLiveActivity(event.title, event.detail);
  } else if (event.type === "comment") {
    liveCount.textContent = `${event.count} collected`;
    liveCount.classList.remove("hidden");
    const line = `@${event.username || "user"}: ${event.comment || ""}`;
    if (commentQueue.length === 0 && !pendingStageDetail) {
      animateDetail(line);
    }
    commentQueue.push({
      username: event.username || "user",
      comment: event.comment || "",
    });
  } else if (event.type === "done") {
    hideLiveActivity();
    setLoading(false);
    renderResults(event.result);
  } else if (event.type === "error") {
    hideLiveActivity();
    setLoading(false);
    setError(event.detail || "Moderation failed.");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();
  if (!url) {
    setError("Please paste an Instagram post or Reel URL first.");
    urlInput.focus();
    return;
  }

  stopStream();
  setLoading(true);
  setError("");
  resultsSection.classList.add("hidden");
  showLiveActivity("Scraping comments", "Opening Instagram post and reading comments...");
  startTicker();

  const streamUrl = `${API_BASE}/moderate/stream?url=${encodeURIComponent(url)}`;
  const es = new EventSource(streamUrl);
  activeEventSource = es;

  es.onmessage = (msg) => {
    try {
      const payload = JSON.parse(msg.data);
      handleStreamEvent(payload);
    } catch {
      // Ignore malformed chunk
    }
  };

  es.onerror = () => {
    if (activeEventSource === null) return;
    hideLiveActivity();
    setLoading(false);
    setError("Lost connection to the backend stream. Make sure python3 main.py is running.");
  };
});
