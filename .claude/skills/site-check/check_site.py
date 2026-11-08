#!/usr/bin/env python3
"""Pre-publish checks for the static site. Run from the repository root."""
import glob, json, os, re, sys
import xml.etree.ElementTree as ET

SITE = "https://rusphiltrade.com"
SKIP_DIRS = (".git", ".claude", "trade-agent", "node_modules")
NOT_IN_SITEMAP = ("thank-you", "business-car-manila")  # unlisted on purpose
problems = []


def local_path(url, base_dir):
    url = url.split("#")[0].split("?")[0]
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None
    if url.startswith(SITE):
        url = url[len(SITE):] or "/"
    elif re.match(r"^[a-z]+:", url) or url.startswith("//"):
        return None
    path = url.lstrip("/") if url.startswith("/") else os.path.normpath(os.path.join(base_dir, url))
    if path in ("", "."):
        return "index.html"
    if os.path.isdir(path):
        path = os.path.join(path, "index.html")
    return path


pages = [p for p in glob.glob("**/*.html", recursive=True) if not p.startswith(SKIP_DIRS)]
for page in pages:
    src = open(page, encoding="utf-8").read()
    base = os.path.dirname(page)
    for attr, url in re.findall(r'\b(href|src)="([^"]+)"', src):
        path = local_path(url, base)
        if path and not os.path.exists(path):
            line = src[: src.find(url)].count("\n") + 1
            problems.append(f"{page}:{line}: broken {attr} {url}")

try:
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [u.text.strip() for u in ET.parse("sitemap.xml").findall(".//s:loc", ns)]
    in_sitemap = set()
    for loc in locs:
        path = local_path(loc, ".")
        in_sitemap.add(path)
        if path and not os.path.exists(path):
            problems.append(f"sitemap.xml: missing page for {loc}")
    for page in pages:
        if page.endswith("index.html") and page not in in_sitemap and not any(s in page for s in NOT_IN_SITEMAP):
            problems.append(f"sitemap.xml: page not listed: {page}")
except ET.ParseError as e:
    problems.append(f"sitemap.xml: invalid XML: {e}")

for feed in glob.glob("**/feed.xml", recursive=True):
    try:
        ET.parse(feed)
    except ET.ParseError as e:
        problems.append(f"{feed}: invalid XML: {e}")

for data in glob.glob("data/*.json"):
    try:
        json.load(open(data, encoding="utf-8"))
    except ValueError as e:
        problems.append(f"{data}: invalid JSON: {e}")

if problems:
    print("\n".join(problems))
    print(f"\n{len(problems)} problem(s)")
    sys.exit(1)
print(f"OK: {len(pages)} pages checked")
