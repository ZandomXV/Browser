"""
cleaner.py — Runs inside GitHub Actions.
Fetches a URL, cleans the HTML, writes result to a GitHub Gist.
"""
import os
import re
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
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
    Preserve images as much as possible.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted tags — but keep <style>, <svg>, <picture>, <source>
    for tag_name in ["script", "noscript", "iframe", "object", "embed",
                     "applet", "canvas", "math", "template"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove <link> tags except stylesheets (they help with layout)
    for tag in soup.find_all("link"):
        if tag.get("rel") and "stylesheet" in tag.get("rel", []):
            # Rewrite stylesheet href to absolute
            if tag.get("href"):
                tag["href"] = urljoin(base_url, tag["href"])
        else:
            tag.decompose()

    # Remove event handler attributes but keep style attributes
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

    # Rewrite form actions to absolute
    for form in soup.find_all("form", action=True):
        act = form["action"]
        if act and not act.startswith(("javascript:", "#")):
            form["action"] = urljoin(base_url, act)

    # Rewrite ALL image-related attributes to absolute URLs
    img_attrs = ["src", "data-src", "data-lazy", "data-lazy-src", "data-original",
                 "data-image", "data-thumb", "data-thumb_url", "data-poster",
                 "poster", "data-bg", "data-background"]
    for img in soup.find_all(["img", "video", "source"]):
        for attr in img_attrs:
            val = img.get(attr)
            if val and not val.startswith("data:"):
                img[attr] = urljoin(base_url, val)
        # Promote lazy-loaded src
        if not img.get("src") or img["src"].startswith("data:"):
            for fallback in ["data-src", "data-lazy-src", "data-lazy", "data-original"]:
                val = img.get(fallback)
                if val and not val.startswith("data:"):
                    img["src"] = val
                    break
        # Rewrite srcset to absolute URLs
        if img.get("srcset"):
            parts = []
            for entry in img["srcset"].split(","):
                entry = entry.strip()
                if not entry:
                    continue
                pieces = entry.split()
                if pieces:
                    pieces[0] = urljoin(base_url, pieces[0])
                parts.append(" ".join(pieces))
            img["srcset"] = ", ".join(parts)

    # Rewrite background-image in inline styles to absolute URLs
    for tag in soup.find_all(style=True):
        style = tag["style"]
        def rewrite_bg_url(match):
            url = match.group(1).strip("\"'")
            if url.startswith("data:"):
                return match.group(0)
            return f"url('{urljoin(base_url, url)}')"
        tag["style"] = re.sub(r"url\(['\"]?([^)]+?)['\"]?\)", rewrite_bg_url, style)

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
  img, picture, video {{ max-width: 100%; height: auto; border-radius: 4px; display: inline-block; }}
  img[src] {{ min-width: 20px; min-height: 20px; }}
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
