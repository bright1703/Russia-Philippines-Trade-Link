"""Build data/chat-knowledge.json for the site chat assistant.

Collects the published facts of every product and service page (title, lead,
tables, lists, FAQ) so the chat backend can answer from what the site already
says instead of deflecting. Run from the repository root after editing pages:

    python3 tools/build_chat_knowledge.py
"""
import glob, html, json, re

SITE = "https://rusphiltrade.com"
SKIP = ("thank-you", "privacy", "terms", "philippine-trade-news", "novosti-filippin", "guides/index.html")


def text(fragment):
    fragment = re.sub(r"<(script|style|svg|form)[^>]*>.*?</\1>", "", fragment, flags=re.S)
    fragment = re.sub(r"</(td|th)>", " | ", fragment)
    fragment = re.sub(r"</(p|li|tr|h2|h3|summary|details|figcaption)>|<br\s*/?>", "\n", fragment)
    fragment = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    lines = [re.sub(r"[ \t]+", " ", l).strip(" |") for l in fragment.splitlines()]
    return "\n".join(l for l in lines if l)


def page(path):
    s = open(path, encoding="utf-8").read()
    if 'name="robots" content="noindex' in s:
        return None
    url = SITE + "/" + path[: -len("index.html")]
    lang = re.search(r'<html lang="(\w+)"', s).group(1)
    title = html.unescape(re.search(r"<title>(.*?)</title>", s, re.S).group(1)).strip()
    desc = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', s).group(1))
    h1 = text(re.search(r"<h1[^>]*>(.*?)</h1>", s, re.S).group(1))
    body = re.search(r"<article[^>]*>(.*?)</article>", s, re.S)
    facts = text(body.group(1)) if body else ""
    # Drop the generic request steps and the source list, keep the substance.
    facts = re.split(r"\n(?:How a request works|How it works|Official sources|Related products and guides|Other directions[^\n]*)\n", "\n" + facts)[0].strip()
    faq = [(text(q), text(a)) for q, a in re.findall(r"<summary>(.*?)</summary><p>(.*?)</p>", s, re.S)]
    return {"url": url, "lang": lang, "title": title, "h1": h1, "description": desc,
            "facts": facts[:6000], "faq": [{"q": q, "a": a} for q, a in faq]}


pages = []
for path in sorted(glob.glob("**/index.html", recursive=True)):
    if path == "index.html" or any(k in path for k in SKIP):
        continue
    p = page(path)
    if p:
        pages.append(p)

directions = json.load(open("data/directions.json", encoding="utf-8"))
for d in directions:
    d["page_url"] = SITE + d["page_url"] if d.get("page_url") else ""
    d.pop("meta_html", None)

out = {
    "about": "Russia-Philippines Trade Link (rusphiltrade.com): independent B2B trade platform and contact point in the Philippines for selected suppliers from Primorsky Krai (Vladivostok) and the Russian Far East. Trade Agent of Primorsky Krai project. Supplier search is free for buyers; buyers contract and pay suppliers directly. Contact: rus.import.ph@gmail.com.",
    "rules": [
        "Answer from these facts when they cover the question, and give the page URL.",
        "Say clearly when a fact is not published (price, MOQ, specification) and offer to request it from the manufacturer.",
        "Never invent prices, certificates, registrations or delivery times.",
    ],
    "directions": directions,
    "pages": pages,
}
json.dump(out, open("data/chat-knowledge.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(pages), "pages,", len(directions), "directions")
