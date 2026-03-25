"""
app.py — Local browser shell using Flask.
Intercepts navigation, routes through GitHub Actions tunnel.
Async loading: returns spinner immediately, polls for result via JS.
"""
import threading
import webbrowser
from flask import Flask, request, jsonify, redirect
from urllib.parse import urlparse, urljoin
from github_tunnel import dispatch_fetch, poll_result, cleanup_gist

app = Flask(__name__)

# In-memory cache: url -> {html, final_url, status}
PAGE_CACHE = {}
# In-flight requests: request_id -> url
IN_FLIGHT = {}
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
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f23; color: #e0e0e0;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; margin: 0;
  }}
  .container {{ text-align: center; max-width: 500px; padding: 24px; }}
  .spinner {{
    width: 50px; height: 50px;
    border: 5px solid #333; border-top-color: #bb86fc;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 16px;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  h2 {{ color: #bb86fc; margin-bottom: 8px; }}
  p {{ color: #888; }}
  .timer {{ font-size: 24px; color: #bb86fc; margin: 12px 0; }}
  .dots::after {{ content: ''; animation: dots 1.5s steps(4,end) infinite; }}
  @keyframes dots {{
    0%, 20% {{ content: ''; }}
    40% {{ content: '.'; }}
    60% {{ content: '..'; }}
    80%, 100% {{ content: '...'; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="spinner"></div>
  <h2>Fetching page<span class="dots"></span></h2>
  <p>{url}</p>
  <div class="timer" id="timer">0s</div>
  <p style="font-size:13px;">Routing through GitHub Actions tunnel</p>
  <p id="status" style="font-size:12px;color:#555;margin-top:8px;">Waiting for GitHub Action to start...</p>
</div>
<script>
var startTime = Date.now();
var rid = "{request_id}";
var timer = document.getElementById("timer");
var status = document.getElementById("status");

setInterval(function() {{
  var elapsed = Math.floor((Date.now() - startTime) / 1000);
  timer.textContent = elapsed + "s";
  if (elapsed > 5) status.textContent = "Action running, fetching page...";
  if (elapsed > 15) status.textContent = "Downloading images...";
  if (elapsed > 30) status.textContent = "Almost there...";
}}, 1000);

function checkResult() {{
  fetch("/poll?request_id=" + rid)
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (data.ready) {{
        document.open();
        document.write(data.html);
        document.close();
      }} else {{
        setTimeout(checkResult, 2000);
      }}
    }})
    .catch(function() {{
      setTimeout(checkResult, 3000);
    }});
}}
setTimeout(checkResult, 3000);
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

    # Start background polling thread
    def bg_poll():
        result = poll_result(request_id, timeout=120, interval=2)
        if result:
            PAGE_CACHE[url] = result.get("html", "")
        # Cleanup old gist files
        try:
            cleanup_gist(keep_latest=3)
        except Exception:
            pass

    threading.Thread(target=bg_poll, daemon=True).start()

    # Return loading page with request_id for JS polling
    return LOADING_PAGE.format(url=url, request_id=request_id)


@app.route("/poll")
def poll_endpoint():
    """JS polls this to check if a page is ready."""
    request_id = request.args.get("request_id", "")
    url = IN_FLIGHT.get(request_id, "")

    # Check if the result has landed in cache
    if url and url in PAGE_CACHE:
        html = rewrite_links(PAGE_CACHE[url], url)
        return jsonify({"ready": True, "html": html})

    # Not ready yet
    return jsonify({"ready": False})


def rewrite_links(html, page_url=""):
    """Rewrite all links to route through /browse?url=..."""
    import re

    # Determine origin from the page URL for resolving relative paths
    origin = ""
    if page_url:
        p = urlparse(page_url)
        origin = f"{p.scheme}://{p.netloc}"

    def proxy_url(url):
        """Convert a URL to a proxied /browse?url=... URL."""
        if not url or url.startswith(("/browse?", "#", "javascript:", "mailto:", "tel:", "NAVIGATE:", "data:")):
            return None
        if url.startswith("//"):
            return f"/browse?url=https:{url}"
        if url.startswith(("http://", "https://")):
            return f"/browse?url={url}"
        # Relative URL — resolve against the page's origin
        if origin and url.startswith("/"):
            return f"/browse?url={origin}{url}"
        if origin and page_url:
            return f"/browse?url={urljoin(page_url, url)}"
        return None

    def replace_href(match):
        prefix = match.group(1)
        url = match.group(2)
        proxied = proxy_url(url)
        if proxied:
            return f'{prefix}{proxied}"'
        return match.group(0)

    # Rewrite href="..." links
    html = re.sub(r'(href=")([^"]*)"', replace_href, html)

    # Rewrite form action="..." to go through proxy
    def replace_action(match):
        prefix = match.group(1)
        url = match.group(2)
        proxied = proxy_url(url)
        if proxied:
            return f'{prefix}{proxied}"'
        return match.group(0)

    html = re.sub(r'(action=")([^"]*)"', replace_action, html)

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
