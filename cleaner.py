"""
cleaner.py — Runs inside GitHub Actions.
Fetches a URL, cleans the HTML, writes result to a GitHub Gist.
"""
import os
import json
import requests
import certifi
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

TARGET_URL = os.environ["TARGET_URL"]
REQUEST_ID = os.environ["REQUEST_ID"]
GH_TOKEN = os.environ["GH_TOKEN"]
GIST_ID = os.environ["GIST_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch(url):
    """Fetch the raw HTML from the target URL."""
    print(f"Fetching: {url}")
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True, verify=certifi.where())
    print(f"Status: {r.status_code}, Length: {len(r.text)}")
    # Don't raise on 4xx — many sites return usable HTML with 4xx codes
    if r.status_code >= 500:
        r.raise_for_status()
    return r.text, r.url, r.status_code


def clean_html(html, base_url):
    """
    Strip JS, ads, heavy assets. Keep readable content + navigation links.
    Rewrite all links to go through our proxy system.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted tags entirely
    for tag_name in ["script", "noscript", "style", "iframe", "object", "embed",
                     "applet", "video", "audio", "canvas", "svg", "math",
                     "template", "link", "meta"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove event handler attributes
    for tag in soup.find_all(True):
        attrs_to_remove = [attr for attr in tag.attrs if attr.startswith("on")]
        for attr in attrs_to_remove:
            del tag[attr]

    # Rewrite all links to absolute URLs (the local app will intercept them)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        a["href"] = absolute

    # Rewrite image sources to absolute
    for img in soup.find_all("img"):
        for attr in ["src", "data-src", "data-lazy"]:
            val = img.get(attr)
            if val and not val.startswith("data:"):
                img[attr] = urljoin(base_url, val)
        # Set src from data-src if src is missing
        if not img.get("src") and img.get("data-src"):
            img["src"] = img["data-src"]

    # Add a minimal base style for readability
    title = soup.title.string if soup.title else urlparse(base_url).netloc
    
    # Build clean HTML
    body = soup.find("body")
    body_content = str(body) if body else str(soup)

    clean = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 16px;
    background: #1a1a2e;
    color: #e0e0e0;
    line-height: 1.6;
  }}
  a {{ color: #64b5f6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  img {{ max-width: 100%; height: auto; border-radius: 4px; }}
  h1, h2, h3 {{ color: #bb86fc; }}
  pre, code {{ background: #16213e; padding: 4px 8px; border-radius: 4px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #333; padding: 8px; }}
  #proxy-nav {{
    position: sticky; top: 0; z-index: 1000;
    background: #16213e; padding: 8px 12px; margin: -16px -16px 16px -16px;
    border-bottom: 2px solid #bb86fc; display: flex; align-items: center; gap: 8px;
  }}
  #proxy-nav input {{
    flex: 1; padding: 6px 10px; border-radius: 4px; border: 1px solid #555;
    background: #0f3460; color: #e0e0e0; font-size: 14px;
  }}
  #proxy-nav button {{
    padding: 6px 16px; border-radius: 4px; border: none;
    background: #bb86fc; color: #1a1a2e; font-weight: bold; cursor: pointer;
  }}
</style>
</head>
<body>
<div id="proxy-nav">
  <span style="font-weight:bold;color:#bb86fc;">🌐</span>
  <input type="text" id="url-bar" value="{base_url}" />
  <button onclick="window.location.href='NAVIGATE:'+document.getElementById('url-bar').value">Go</button>
</div>
{body_content}
</body>
</html>"""
    return clean


def write_to_gist(request_id, html, status_code, final_url):
    """Write the result to a GitHub Gist so the local app can poll it."""
    result = {
        "request_id": request_id,
        "status": status_code,
        "url": final_url,
        "html": html,
    }

    # Write as a JSON file in the gist
    filename = f"{request_id}.json"
    payload = {
        "files": {
            filename: {
                "content": json.dumps(result)
            }
        }
    }

    r = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    print(f"Gist updated: {r.status_code}")


def main():
    try:
        html, final_url, status_code = fetch(TARGET_URL)
        cleaned = clean_html(html, final_url)
        write_to_gist(REQUEST_ID, cleaned, status_code, final_url)
        print("Done! Result written to gist.")
    except Exception as e:
        error_html = f"<html><body><h1>Error</h1><p>{str(e)}</p><p>URL: {TARGET_URL}</p></body></html>"
        write_to_gist(REQUEST_ID, error_html, 500, TARGET_URL)
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
