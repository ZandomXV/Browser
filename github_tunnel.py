"""
github_tunnel.py — Dispatch workflow + poll Gist for results.
This is the communication layer between the local app and GitHub Actions.
"""
import os
import json
import time
import uuid
from dotenv import load_dotenv
from ssl_helper import create_session

load_dotenv()

GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO_OWNER = os.environ.get("REPO_OWNER", "")
REPO_NAME = os.environ.get("REPO_NAME", "")
GIST_ID = os.environ.get("GIST_ID", "")
WORKFLOW_FILE = "fetch.yml"

API_BASE = "https://api.github.com"
SESSION = create_session()


def _headers():
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def dispatch_fetch(url):
    """
    Trigger the GitHub Actions workflow to fetch a URL.
    Returns the request_id used to track this request.
    """
    request_id = uuid.uuid4().hex[:12]
    endpoint = f"{API_BASE}/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"

    payload = {
        "ref": "main",
        "inputs": {
            "url": url,
            "request_id": request_id,
        },
    }

    r = SESSION.post(endpoint, headers=_headers(), json=payload, timeout=15)
    if r.status_code == 204:
        print(f"[tunnel] Dispatched workflow for {url} (id={request_id})")
        return request_id
    else:
        raise RuntimeError(f"Dispatch failed: {r.status_code} {r.text}")


def poll_result(request_id, timeout=60, interval=2):
    """
    Poll the Gist for the result of a given request_id.
    Returns the parsed result dict or None on timeout.
    """
    filename = f"{request_id}.json"
    endpoint = f"{API_BASE}/gists/{GIST_ID}"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            r = SESSION.get(endpoint, headers=_headers(), timeout=10)
            if r.status_code == 200:
                gist_data = r.json()
                files = gist_data.get("files", {})
                if filename in files:
                    content = files[filename].get("content", "")
                    result = json.loads(content)
                    if result.get("request_id") == request_id:
                        print(f"[tunnel] Got result for {request_id}")
                        return result
        except Exception as e:
            print(f"[tunnel] Poll error: {e}")
        time.sleep(interval)

    print(f"[tunnel] Timeout waiting for {request_id}")
    return None


def fetch_page(url, timeout=90):
    """
    High-level: dispatch a fetch and wait for the result.
    Returns (html, final_url, status) or raises on failure.
    """
    request_id = dispatch_fetch(url)
    result = poll_result(request_id, timeout=timeout)

    if result is None:
        return (
            "<html><body><h1>Timeout</h1><p>The page took too long to fetch. Try again.</p></body></html>",
            url,
            504,
        )

    return result.get("html", ""), result.get("url", url), result.get("status", 200)


def poll_image_progress(request_id):
    """Check the gist for image download progress from the Action."""
    filename = f"{request_id}_progress.json"
    try:
        r = SESSION.get(f"{API_BASE}/gists/{GIST_ID}", headers=_headers(), timeout=10)
        if r.status_code == 200:
            files = r.json().get("files", {})
            if filename in files:
                content = files[filename].get("content", "")
                data = json.loads(content)
                return {
                    "images": data.get("images", []),
                    "downloaded": data.get("downloaded", 0),
                    "total": data.get("total", 0),
                    "done": data.get("done", False),
                }
    except Exception as e:
        print(f"[tunnel] Image progress poll error: {e}")
    return {"images": [], "downloaded": 0, "total": 0, "done": False}


def cleanup_gist(keep_latest=5):
    """Remove old request files from the gist to keep it clean."""
    try:
        r = SESSION.get(f"{API_BASE}/gists/{GIST_ID}", headers=_headers(), timeout=10)
        if r.status_code == 200:
            files = r.json().get("files", {})
            json_files = [f for f in files if f.endswith(".json")]
            if len(json_files) > keep_latest:
                to_delete = json_files[:-keep_latest]
                payload = {"files": {f: None for f in to_delete}}
                SESSION.patch(
                    f"{API_BASE}/gists/{GIST_ID}",
                    headers=_headers(),
                    json=payload,
                    timeout=10,
                )
                print(f"[tunnel] Cleaned up {len(to_delete)} old gist files")
    except Exception as e:
        print(f"[tunnel] Cleanup error: {e}")
