"""
app.py — Local browser shell using Flask + webview.
Intercepts navigation, routes through GitHub Actions tunnel.
"""
import threading
import webbrowser
from flask import Flask, request, render_template_string, jsonify
from github_tunnel import fetch_page, cleanup_gist

app = Flask(__name__)

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
<title>Loading...</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f23; color: #e0e0e0;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; margin: 0;
  }}
  .container {{ text-align: center; }}
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
</style>
<meta http-equiv="refresh" content="2;url=/poll?url={url}&request_id={request_id}">
</head>
<body>
<div class="container">
  <div class="spinner"></div>
  <h2>Fetching page...</h2>
  <p>{url}</p>
  <p style="font-size:13px;margin-top:12px;">Routing through GitHub Actions tunnel</p>
</div>
</body>
</html>"""


@app.route("/")
def home():
    return HOME_PAGE


@app.route("/browse")
def browse():
    url = request.args.get("url", "").strip()
    if not url:
        return HOME_PAGE

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch the page through the tunnel (blocking — takes ~10-20s)
    html, final_url, status = fetch_page(url, timeout=90)

    # Rewrite links in the returned HTML to go through our proxy
    html = rewrite_links(html)

    # Periodic cleanup
    try:
        cleanup_gist(keep_latest=3)
    except Exception:
        pass

    return html


def rewrite_links(html):
    """Rewrite absolute links to route through /browse?url=..."""
    import re

    def replace_href(match):
        prefix = match.group(1)
        url = match.group(2)
        # Skip already-proxied links, anchors, javascript, mailto, data
        if url.startswith(("/browse?", "#", "javascript:", "mailto:", "tel:", "NAVIGATE:", "data:")):
            return match.group(0)
        # Protocol-relative URLs like //en.m.facebook.com/...
        if url.startswith("//"):
            return f'{prefix}/browse?url=https:{url}"'
        # Absolute URLs
        if url.startswith(("http://", "https://")):
            return f'{prefix}/browse?url={url}"'
        return match.group(0)

    # Rewrite href="..." links
    html = re.sub(r'(href=")([^"]*)"', replace_href, html)

    # Rewrite form action="..." to go through proxy
    def replace_action(match):
        prefix = match.group(1)
        url = match.group(2)
        if url.startswith(("/browse?", "#", "javascript:", "data:")):
            return match.group(0)
        if url.startswith("//"):
            return f'{prefix}/browse?url=https:{url}"'
        if url.startswith(("http://", "https://")):
            return f'{prefix}/browse?url={url}"'
        return match.group(0)

    html = re.sub(r'(action=")([^"]*)"', replace_action, html)

    # Rewrite the Go button's NAVIGATE: handler
    html = html.replace(
        "window.location.href='NAVIGATE:'+document.getElementById('url-bar').value",
        "window.location.href='/browse?url='+encodeURIComponent(document.getElementById('url-bar').value)"
    )

    return html


if __name__ == "__main__":
    print("=" * 50)
    print("  GitHub Tunnel Browser")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    webbrowser.open("http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
