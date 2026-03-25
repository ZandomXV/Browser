"""
app.py — Local browser shell using Flask.
Intercepts navigation, routes through GitHub Actions tunnel.
Async loading: returns spinner immediately, polls for result via JS.
"""
import threading
import webbrowser
import time
import re
import html as html_mod
from flask import Flask, request, jsonify, redirect
from urllib.parse import urlparse, urljoin
from github_tunnel import dispatch_fetch, poll_result, cleanup_gist, poll_image_progress

app = Flask(__name__)

# In-memory cache: url -> {html, final_url, status}
PAGE_CACHE = {}
# In-flight requests: request_id -> url
IN_FLIGHT = {}
# Progress tracking: request_id -> {percent, stage}
PROGRESS = {}
# Track last browsed origin for resolving relative URLs
LAST_ORIGIN = {"value": ""}

HOME_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitHub Tunnel Browser</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f23;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .container {
    text-align: center;
    padding: 40px;
  }
  h1 {
    font-size: 2.5rem;
    background: linear-gradient(135deg, #bb86fc, #64b5f6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .subtitle {
    color: #888;
    margin-bottom: 32px;
    font-size: 0.95rem;
  }
  .search-box {
    display: flex;
    gap: 8px;
    max-width: 600px;
    margin: 0 auto 24px;
  }
  .search-box input {
    flex: 1;
    padding: 12px 16px;
    border-radius: 8px;
    border: 2px solid #333;
    background: #1a1a2e;
    color: #e0e0e0;
    font-size: 16px;
    outline: none;
    transition: border-color 0.2s;
  }
  .search-box input:focus { border-color: #bb86fc; }
  .search-box button {
    padding: 12px 24px;
    border-radius: 8px;
    border: none;
    background: linear-gradient(135deg, #bb86fc, #6c63ff);
    color: white;
    font-weight: bold;
    font-size: 16px;
    cursor: pointer;
    transition: transform 0.1s;
  }
  .search-box button:hover { transform: scale(1.02); }
  .quick-links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
    max-width: 600px;
    margin: 0 auto;
  }
  .quick-links a {
    padding: 8px 16px;
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 20px;
    color: #64b5f6;
    text-decoration: none;
    font-size: 14px;
    transition: all 0.2s;
  }
  .quick-links a:hover {
    background: #16213e;
    border-color: #bb86fc;
    color: #bb86fc;
  }
  .status {
    margin-top: 24px;
    color: #888;
    font-size: 13px;
  }
  #loading {
    display: none;
    margin-top: 24px;
  }
  .spinner {
    width: 40px; height: 40px;
    border: 4px solid #333;
    border-top-color: #bb86fc;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 12px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <h1>GitHub Tunnel Browser</h1>
  <p class="subtitle">Browse the web through GitHub Actions — free & unrestricted</p>

  <form class="search-box" onsubmit="navigate(event)">
    <input type="text" id="url-input" placeholder="Enter URL (e.g. https://wikipedia.org)" autofocus />
    <button type="submit">Go</button>
  </form>

  <div class="quick-links">
    <a href="/browse?url=https://en.wikipedia.org">Wikipedia</a>
    <a href="/browse?url=https://news.ycombinator.com">Hacker News</a>
    <a href="/browse?url=https://lite.duckduckgo.com">DuckDuckGo</a>
    <a href="/browse?url=https://old.reddit.com">Reddit</a>
    <a href="/browse?url=https://text.npr.org">NPR Text</a>
    <a href="/browse?url=https://www.google.com">Google</a>
  </div>

  <div id="loading">
    <div class="spinner"></div>
    <p>Fetching page via GitHub Actions...</p>
    <p class="status">This usually takes 10-20 seconds</p>
  </div>
</div>

<script>
function navigate(e) {
  e.preventDefault();
  var url = document.getElementById('url-input').value.trim();
  if (!url) return;
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
  }
  document.getElementById('loading').style.display = 'block';
  window.location.href = '/browse?url=' + encodeURIComponent(url);
}
</script>
</body>
</html>"""

LOADING_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Loading - {url}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f23; color: #e0e0e0;
    min-height: 100vh;
  }}
  .top-bar {{
    padding: 12px 24px;
    background: #12122a;
    border-bottom: 1px solid #222;
    text-align: center;
    position: sticky; top: 0; z-index: 100;
  }}
  .top-bar h2 {{ color: #bb86fc; font-size: 1.2rem; margin-bottom: 6px; }}
  .top-bar .url {{ color: #64b5f6; font-size: 13px; word-break: break-all; }}
  .progress-section {{
    padding: 10px 24px;
    background: #0d0d1f;
    border-bottom: 1px solid #1a1a33;
    position: sticky; top: 58px; z-index: 99;
  }}
  .progress-wrap {{
    width: 100%; height: 24px;
    background: #1a1a2e; border-radius: 12px;
    overflow: hidden; border: 1px solid #333;
    position: relative; margin-bottom: 6px;
  }}
  .progress-bar {{
    height: 100%; border-radius: 12px;
    background: linear-gradient(90deg, #6c63ff, #bb86fc, #64b5f6);
    background-size: 200% 100%;
    animation: shimmer 2s ease infinite;
    transition: width 0.5s ease; width: 0%;
  }}
  @keyframes shimmer {{ 0%{{background-position:200% 0}} 100%{{background-position:-200% 0}} }}
  .progress-text {{
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 12px; color: #fff;
    text-shadow: 0 1px 3px rgba(0,0,0,0.7);
  }}
  .info-row {{
    display: flex; justify-content: space-between; align-items: center;
    font-size: 13px;
  }}
  .stage {{ color: #999; }}
  .timer {{ color: #666; }}
  .gallery-section {{
    padding: 16px;
  }}
  .gallery-header {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 12px; display: none;
  }}
  .gallery-header h3 {{ color: #bb86fc; font-size: 14px; }}
  .gallery-header .badge {{
    background: #6c63ff; color: #fff; padding: 2px 10px;
    border-radius: 10px; font-size: 12px; font-weight: bold;
  }}
  .gallery {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 10px;
  }}
  .gallery .thumb {{
    position: relative; aspect-ratio: 1;
    border-radius: 8px; overflow: hidden;
    border: 2px solid #222; cursor: pointer;
    transition: border-color 0.2s, transform 0.2s;
    background: #1a1a2e;
  }}
  .gallery .thumb:hover {{
    border-color: #bb86fc; transform: scale(1.03);
    z-index: 1;
  }}
  .gallery .thumb img {{
    width: 100%; height: 100%; object-fit: cover;
    opacity: 0; animation: fadeIn 0.5s ease forwards;
  }}
  @keyframes fadeIn {{ to {{ opacity: 1; }} }}
  .gallery .thumb .new-badge {{
    position: absolute; top: 4px; right: 4px;
    background: #6c63ff; color: #fff; font-size: 9px;
    padding: 1px 6px; border-radius: 6px; font-weight: bold;
    animation: fadeIn 0.3s ease forwards;
  }}
  .empty-msg {{
    text-align: center; color: #444; padding: 40px 20px;
    font-size: 14px;
  }}

  /* Lightbox */
  .lightbox {{
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.92); z-index: 9999;
    flex-direction: column; align-items: center; justify-content: center;
  }}
  .lightbox.open {{ display: flex; animation: fadeIn 0.2s ease; }}
  .lightbox img {{
    max-width: 90vw; max-height: 80vh; border-radius: 8px;
    box-shadow: 0 8px 40px rgba(0,0,0,0.5);
  }}
  .lightbox .close-btn {{
    position: absolute; top: 16px; right: 20px;
    background: none; border: none; color: #fff; font-size: 32px;
    cursor: pointer; opacity: 0.7; transition: opacity 0.2s;
  }}
  .lightbox .close-btn:hover {{ opacity: 1; }}
  .lightbox .nav-btn {{
    position: absolute; top: 50%; transform: translateY(-50%);
    background: rgba(255,255,255,0.1); border: none; color: #fff;
    font-size: 28px; padding: 12px 16px; cursor: pointer;
    border-radius: 8px; transition: background 0.2s;
  }}
  .lightbox .nav-btn:hover {{ background: rgba(255,255,255,0.2); }}
  .lightbox .nav-prev {{ left: 16px; }}
  .lightbox .nav-next {{ right: 16px; }}
  .lightbox .img-counter {{
    color: #888; font-size: 13px; margin-top: 12px;
  }}
</style>
</head>
<body>

<div class="top-bar">
  <h2>Loading page...</h2>
  <div class="url">{url}</div>
</div>

<div class="progress-section">
  <div class="progress-wrap">
    <div class="progress-bar" id="bar"></div>
    <div class="progress-text" id="pct">0%</div>
  </div>
  <div class="info-row">
    <span class="stage" id="stage">Starting...</span>
    <span class="timer" id="timer">0s</span>
  </div>
</div>

<div class="gallery-section">
  <div class="gallery-header" id="gheader">
    <h3>Images downloaded</h3>
    <span class="badge" id="imgcount">0</span>
  </div>
  <div class="gallery" id="gallery"></div>
  <div class="empty-msg" id="emptymsg">Waiting for images to download...</div>
</div>

<!-- Lightbox -->
<div class="lightbox" id="lightbox">
  <button class="close-btn" id="lb-close">&times;</button>
  <button class="nav-btn nav-prev" id="lb-prev">&#8249;</button>
  <button class="nav-btn nav-next" id="lb-next">&#8250;</button>
  <img id="lb-img" src="" />
  <div class="img-counter" id="lb-counter"></div>
</div>

<script>
var startTime = Date.now();
var rid = "{request_id}";
var bar = document.getElementById("bar");
var pctEl = document.getElementById("pct");
var stageEl = document.getElementById("stage");
var timerEl = document.getElementById("timer");
var gallery = document.getElementById("gallery");
var gheader = document.getElementById("gheader");
var imgcount = document.getElementById("imgcount");
var emptymsg = document.getElementById("emptymsg");
var shownImages = 0;
var pageReady = false;
var allImageSrcs = [];

// Timer
setInterval(function() {{
  var s = Math.floor((Date.now() - startTime) / 1000);
  var m = Math.floor(s / 60);
  timerEl.textContent = m > 0 ? m + "m " + (s % 60) + "s" : s + "s";
}}, 1000);

// Lightbox
var lbImg = document.getElementById("lb-img");
var lbCounter = document.getElementById("lb-counter");
var lightbox = document.getElementById("lightbox");
var lbIndex = 0;

function openLightbox(idx) {{
  lbIndex = idx;
  lbImg.src = allImageSrcs[idx];
  lbCounter.textContent = (idx + 1) + " / " + allImageSrcs.length;
  lightbox.classList.add("open");
}}
function closeLightbox() {{ lightbox.classList.remove("open"); }}
function lbPrev() {{
  lbIndex = (lbIndex - 1 + allImageSrcs.length) % allImageSrcs.length;
  lbImg.src = allImageSrcs[lbIndex];
  lbCounter.textContent = (lbIndex + 1) + " / " + allImageSrcs.length;
}}
function lbNext() {{
  lbIndex = (lbIndex + 1) % allImageSrcs.length;
  lbImg.src = allImageSrcs[lbIndex];
  lbCounter.textContent = (lbIndex + 1) + " / " + allImageSrcs.length;
}}
document.getElementById("lb-close").onclick = closeLightbox;
document.getElementById("lb-prev").onclick = lbPrev;
document.getElementById("lb-next").onclick = lbNext;
lightbox.onclick = function(e) {{ if (e.target === lightbox) closeLightbox(); }};
document.addEventListener("keydown", function(e) {{
  if (!lightbox.classList.contains("open")) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") lbPrev();
  if (e.key === "ArrowRight") lbNext();
}});

// Image polling
function checkImages() {{
  if (pageReady) return;
  fetch("/poll_images?request_id=" + rid)
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      var imgs = data.images || [];
      if (imgs.length > shownImages) {{
        gheader.style.display = "flex";
        emptymsg.style.display = "none";
        for (var i = shownImages; i < imgs.length; i++) {{
          var idx = allImageSrcs.length;
          allImageSrcs.push(imgs[i]);
          var thumb = document.createElement("div");
          thumb.className = "thumb";
          thumb.setAttribute("data-idx", idx);
          thumb.onclick = (function(j) {{ return function() {{ openLightbox(j); }}; }})(idx);
          var img = document.createElement("img");
          img.src = imgs[i];
          var badge = document.createElement("span");
          badge.className = "new-badge";
          badge.textContent = "NEW";
          setTimeout((function(b){{ return function(){{ b.remove(); }}; }})(badge), 3000);
          thumb.appendChild(img);
          thumb.appendChild(badge);
          gallery.appendChild(thumb);
        }}
        shownImages = imgs.length;
      }}
      if (data.total > 0) {{
        imgcount.textContent = data.downloaded + " / " + data.total;
      }}
      if (!data.done && !pageReady) {{
        setTimeout(checkImages, 4000);
      }}
    }})
    .catch(function() {{
      if (!pageReady) setTimeout(checkImages, 5000);
    }});
}}
setTimeout(checkImages, 8000);

// Page result polling
function checkResult() {{
  fetch("/poll?request_id=" + rid)
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      var p = data.percent || 0;
      bar.style.width = p + "%";
      pctEl.textContent = p + "%";
      stageEl.textContent = data.stage || "Working...";
      if (data.ready) {{
        pageReady = true;
        stageEl.textContent = "Rendering page...";
        bar.style.width = "100%";
        pctEl.textContent = "100%";
        setTimeout(function() {{
          document.open();
          document.write(data.html);
          document.close();
        }}, 500);
      }} else if (p === 0 && data.stage && data.stage.indexOf("Timed out") >= 0) {{
        pageReady = true;
        stageEl.textContent = "Timed out. Reloading...";
        setTimeout(function() {{ location.reload(); }}, 2000);
      }} else {{
        setTimeout(checkResult, 2000);
      }}
    }})
    .catch(function() {{
      setTimeout(checkResult, 3000);
    }});
}}
setTimeout(checkResult, 2000);
</script>
</body>
</html>"""


@app.route("/")
def home():
    return HOME_PAGE


@app.route("/browse")
def browse():
    """Start a fetch and return a loading page immediately."""
    url = request.args.get("url", "").strip()
    if not url:
        return HOME_PAGE

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Track the origin for catch-all relative URL resolution
    parsed = urlparse(url)
    LAST_ORIGIN["value"] = f"{parsed.scheme}://{parsed.netloc}"

    # Check cache first
    if url in PAGE_CACHE:
        html = rewrite_links(PAGE_CACHE[url], url)
        return html

    # Dispatch the workflow in a background thread
    request_id = dispatch_fetch(url)
    IN_FLIGHT[request_id] = url

    # Track progress
    PROGRESS[request_id] = {"percent": 5, "stage": "Dispatching GitHub Action..."}

    def bg_poll():
        start = time.time()
        PROGRESS[request_id] = {"percent": 10, "stage": "Waiting for runner..."}
        # Single poll_result call — it handles the loop internally
        result = poll_result(request_id, timeout=90, interval=3)
        elapsed = int(time.time() - start)
        if result:
            PROGRESS[request_id] = {"percent": 100, "stage": "Done!"}
            PAGE_CACHE[url] = result.get("html", "")
            print(f"[app] Page ready in {elapsed}s")
        else:
            PROGRESS[request_id] = {"percent": 0, "stage": "Timed out. Try again."}
        try:
            cleanup_gist(keep_latest=3)
        except Exception:
            pass

    threading.Thread(target=bg_poll, daemon=True).start()

    # Update progress in a separate lightweight thread
    def progress_ticker():
        for i in range(45):
            time.sleep(2)
            if PROGRESS.get(request_id, {}).get("percent", 0) >= 100:
                return
            pct = min(10 + i * 2, 95)
            if i < 4:
                stage = "Waiting for runner..."
            elif i < 8:
                stage = "Fetching page..."
            elif i < 15:
                stage = "Downloading images..."
            else:
                stage = "Almost done..."
            PROGRESS[request_id] = {"percent": pct, "stage": stage}

    threading.Thread(target=progress_ticker, daemon=True).start()

    # Return loading page with request_id for JS polling
    return LOADING_PAGE.format(url=url, request_id=request_id)


@app.route("/poll_images")
def poll_images_endpoint():
    """Return image previews downloaded so far by the Action."""
    request_id = request.args.get("request_id", "")
    if not request_id:
        return jsonify({"images": [], "downloaded": 0, "total": 0})
    data = poll_image_progress(request_id)
    return jsonify(data)


@app.route("/poll")
def poll_endpoint():
    """JS polls this to check if a page is ready."""
    request_id = request.args.get("request_id", "")
    url = IN_FLIGHT.get(request_id, "")

    prog = PROGRESS.get(request_id, {"percent": 0, "stage": "Starting..."})

    # Check if the result has landed in cache
    if url and url in PAGE_CACHE:
        html = rewrite_links(PAGE_CACHE[url], url)
        return jsonify({"ready": True, "html": html, "percent": 100, "stage": "Done!"})

    # Not ready yet — return progress
    return jsonify({"ready": False, "percent": prog["percent"], "stage": prog["stage"]})


def rewrite_links(html, page_url=""):
    """Rewrite all links to route through /browse?url=..."""
    # Determine origin from the page URL for resolving relative paths
    origin = ""
    if page_url:
        p = urlparse(page_url)
        origin = f"{p.scheme}://{p.netloc}"

    def make_proxy_url(url):
        """Convert a URL to a proxied /browse?url=... URL."""
        if not url:
            return None
        # Decode HTML entities like &amp; -> & before processing
        url = html_mod.unescape(url.strip())
        if url.startswith(("/browse?", "#", "javascript:", "mailto:", "tel:", "NAVIGATE:", "data:")):
            return None
        if url.startswith("//"):
            return "/browse?url=https:" + url
        if url.startswith(("http://", "https://")):
            return "/browse?url=" + url
        # Relative URL
        if origin:
            if url.startswith("/"):
                absolute = origin + url
            elif page_url:
                absolute = urljoin(page_url, url)
            else:
                return None
            return "/browse?url=" + absolute
        return None

    # Rewrite href=, action=, both double and single quoted
    def replace_attr(match):
        attr = match.group(1)   # e.g. href=" or action='
        url = match.group(2)
        q = match.group(3)      # closing quote
        proxied = make_proxy_url(url)
        if proxied:
            return f'{attr}{proxied}{q}'
        return match.group(0)

    # Match href="..." href='...' action="..." action='...'
    html = re.sub(r'''((?:href|action)=["'])([^"']*?)(["'])''', replace_attr, html)

    # Rewrite the Go button's NAVIGATE: handler
    html = html.replace(
        "window.location.href='NAVIGATE:'+document.getElementById('url-bar').value",
        "window.location.href='/browse?url='+encodeURIComponent(document.getElementById('url-bar').value)"
    )

    return html


@app.route("/<path:path>")
def catch_all(path):
    """
    Catch-all route: any relative URL that hits our server gets resolved
    against the last browsed origin and proxied through /browse.
    """
    origin = LAST_ORIGIN.get("value", "")
    if not origin:
        return HOME_PAGE

    # Reconstruct the full URL from the relative path + query string
    full_path = f"/{path}"
    qs = request.query_string.decode("utf-8")
    if qs:
        full_path += f"?{qs}"

    target = f"{origin}{full_path}"
    return redirect(f"/browse?url={target}")


if __name__ == "__main__":
    print("=" * 50)
    print("  GitHub Tunnel Browser")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    webbrowser.open("http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
