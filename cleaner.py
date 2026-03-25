"""
cleaner.py — Runs inside GitHub Actions.
Fetches a URL, cleans the HTML, writes result to a GitHub Gist.
"""
import os
import re
import json
import base64
import mimetypes
import requests
import certifi
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# Max total size for inlined images (3MB — gist API struggles with larger)
MAX_IMAGES_BYTES = 3 * 1024 * 1024
# Max single image size (200KB — keeps more images within budget)
MAX_SINGLE_IMAGE = 200 * 1024
# Max number of images to inline
MAX_IMAGES = 200


IMG_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "",
}


def download_image(url):
    """Download a single image and return (url, mime_type, base64_data) or None."""
    try:
        hdrs = dict(IMG_HEADERS)
        # Set referer to the image's origin to avoid hotlink blocks
        from urllib.parse import urlparse as _urlparse
        p = _urlparse(url)
        hdrs["Referer"] = f"{p.scheme}://{p.netloc}/"
        # Try with SSL verification first, fall back without
        try:
            r = requests.get(url, headers=hdrs, timeout=15, verify=certifi.where())
        except requests.exceptions.SSLError:
            r = requests.get(url, headers=hdrs, timeout=15, verify=False)
        if r.status_code != 200:
            return None
        content_type = r.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            guessed, _ = mimetypes.guess_type(url)
            if guessed and guessed.startswith("image/"):
                content_type = guessed
            else:
                return None
        data = r.content
        if len(data) > MAX_SINGLE_IMAGE:
            return None
        if len(data) < 100:
            return None  # skip tiny/empty images
        b64 = base64.b64encode(data).decode("ascii")
        mime = content_type.split(";")[0].strip()
        return (url, mime, b64, len(data))
    except Exception:
        return None


def write_progress(request_id, images, downloaded, total, done=False):
    """Write image preview progress to a separate gist file."""
    try:
        progress = {
            "request_id": request_id,
            "images": images[-20:],  # last 20 thumbnails to keep size small
            "downloaded": downloaded,
            "total": total,
            "done": done,
        }
        filename = f"{request_id}_progress.json"
        payload = {
            "files": {
                filename: {
                    "content": json.dumps(progress)
                }
            }
        }
        requests.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={
                "Authorization": f"token {GH_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"Progress write error: {e}")


def inline_images(soup, base_url):
    """
    Find all images in the soup, download them in parallel,
    and replace src with base64 data URIs.
    """
    # Collect image URLs to download
    img_tags = []
    urls_to_fetch = {}

    for img in soup.find_all("img"):
        src = None
        # Try various source attributes
        for attr in ["src", "data-src", "data-lazy-src", "data-lazy", "data-original"]:
            val = img.get(attr)
            if val and not val.startswith("data:"):
                src = urljoin(base_url, val)
                break
        if src and src.startswith("http"):
            img_tags.append((img, src))
            urls_to_fetch[src] = None  # deduplicate

    # Remove srcset — we can't inline those
    for img in soup.find_all(["img", "source"]):
        if img.get("srcset"):
            del img["srcset"]

    if not urls_to_fetch:
        return

    # Limit number of images
    url_list = list(urls_to_fetch.keys())[:MAX_IMAGES]
    total_expected = len(url_list)
    print(f"Downloading {total_expected} images...")

    # Download in parallel with progress streaming
    results = {}
    preview_uris = []
    total_bytes = 0
    count = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(download_image, u): u for u in url_list}
        for future in as_completed(futures):
            result = future.result()
            if result:
                url, mime, b64, size = result
                if total_bytes + size <= MAX_IMAGES_BYTES:
                    data_uri = f"data:{mime};base64,{b64}"
                    results[url] = data_uri
                    total_bytes += size
                    count += 1
                    # Keep small images as previews for loading screen
                    if len(b64) < 50000:
                        preview_uris.append(data_uri)
                    # Stream progress every 5 images
                    if count % 5 == 0:
                        write_progress(REQUEST_ID, preview_uris, count, total_expected)

    # Final progress
    write_progress(REQUEST_ID, preview_uris, count, total_expected, done=True)
    print(f"Inlined {len(results)} images ({total_bytes // 1024}KB total)")

    # Replace src in img tags
    for img, src in img_tags:
        if src in results:
            img["src"] = results[src]
            # Clean up lazy-load attrs since we have the real data now
            for attr in ["data-src", "data-lazy-src", "data-lazy", "data-original",
                         "loading"]:
                if img.get(attr):
                    del img[attr]


def clean_html(html, base_url):
    """
    Strip JS, ads, heavy assets. Keep readable content + navigation links.
    Download and inline images as base64.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted tags
    for tag_name in ["script", "noscript", "iframe", "object", "embed",
                     "applet", "canvas", "math", "template"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove ALL <link> tags — external stylesheets won't load through firewall
    for tag in soup.find_all("link"):
        tag.decompose()

    # Remove event handler attributes
    for tag in soup.find_all(True):
        attrs_to_remove = [attr for attr in tag.attrs if attr.startswith("on")]
        for attr in attrs_to_remove:
            del tag[attr]

    # Rewrite all links to absolute URLs
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        a["href"] = urljoin(base_url, href)

    # Rewrite form actions to absolute
    for form in soup.find_all("form", action=True):
        act = form["action"]
        if act and not act.startswith(("javascript:", "#")):
            form["action"] = urljoin(base_url, act)

    # Download and inline images as base64
    inline_images(soup, base_url)

    # Inline background-image URLs as well (leave as-is, user's browser won't load them
    # but at least keep the style structure)
    for tag in soup.find_all(style=True):
        style = tag["style"]
        def rewrite_bg_url(match):
            url = match.group(1).strip("\"'")
            if url.startswith("data:"):
                return match.group(0)
            return f"url('{urljoin(base_url, url)}')"
        tag["style"] = re.sub(r"url\(['\"]?([^)]+?)['\"]?\)", rewrite_bg_url, style)

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
