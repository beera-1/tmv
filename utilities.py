import asyncio
import logging
import re
import traceback
from datetime import datetime
from urllib.parse import urljoin

import cloudscraper
from aiohttp import web, ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from database import db
from configs import *
from pyrogram import Client

# Setup Cloudscraper for Cloudflare protection bypass
scraper = cloudscraper.create_scraper(
    delay=10,
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

# Safe URL join
def get_full_url(url: str) -> str:
    return url if url.startswith("http") else urljoin(BASE_URL, url)

# Async fetch using threads for blocking HTTP
async def fetch(url):
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, lambda: scraper.get(url, timeout=15))
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Error fetching {url}: {str(e)}")
        return None

def get_size_in_bytes(size_str):
    size_str = size_str.lower()
    size_match = re.search(r"([\d.]+)\s*(gb|mb)", size_str)
    if size_match:
        size_value = float(size_match.group(1))
        unit = size_match.group(2)
        return size_value * (1024 ** 3 if unit == "gb" else 1024 ** 2)
    return None

async def parse_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/index.php?/forums/topic/" in href and href not in links:
            links.append(href)
        if len(links) == 20:
            break
    return links

async def fetch_attachments(page_url):
    full_url = get_full_url(page_url)
    html = await fetch(full_url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    episode_pattern = re.compile(r"E(?:P)?(\d{1,2})", re.IGNORECASE)
    non_episode_regex = re.compile(r"S(\d{1,2})\s*(?:E|EP)?\s*\(?(\d+(?:-\d+))\)?", re.IGNORECASE)
    domain_removal_regex = re.compile(r"(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    mkv_torrent_removal_regex = re.compile(r"\.mkv\.torrent$")

    content_div = soup.find("div", class_="cPost_contentWrap")
    img_url = content_div.find("img")["src"] if content_div and content_div.find("img") else None

    links, season_based_links, highest_episode_links = [], [], []
    highest_episode_number = highest_season = 0
    highest_episode_range = (0, 0)

    for link in soup.find_all("a", href=True):
        if "attachment.php" not in link["href"]:
            continue

        size_tag = link.find_next("span", string=re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB)", re.I))
        size_in_bytes = get_size_in_bytes(size_tag.text) if size_tag else None

        link_text = link.get_text(strip=True)
        clean_text = mkv_torrent_removal_regex.sub("", domain_removal_regex.sub("", link_text)).strip()

        # Season-based match
        season_match = non_episode_regex.search(link_text)
        if season_match:
            season_number = int(season_match.group(1))
            ep_range = season_match.group(2)
            episode_start, episode_end = map(int, ep_range.split("-")) if "-" in ep_range else (int(ep_range), int(ep_range))

            if season_number > highest_season or (
                season_number == highest_season and episode_end > highest_episode_range[1]
            ):
                highest_season = season_number
                highest_episode_range = (episode_start, episode_end)
                season_based_links = [{"name": clean_text, "link": get_full_url(link["href"])}]
            elif season_number == highest_season and episode_start <= highest_episode_range[1]:
                season_based_links.append({"name": clean_text, "link": get_full_url(link["href"])})

        # Episode match
        episode_matches = episode_pattern.findall(link_text)
        if episode_matches and size_in_bytes and size_in_bytes < 4 * 1024**3:
            ep_num = max(int(ep) for ep in episode_matches)
            if ep_num > highest_episode_number:
                highest_episode_number = ep_num
                highest_episode_links = [{"name": clean_text, "link": get_full_url(link["href"])}]
            elif ep_num == highest_episode_number:
                highest_episode_links.append({"name": clean_text, "link": get_full_url(link["href"])})

        elif size_in_bytes and size_in_bytes < 4 * 1024**3:
            links.append({"name": clean_text, "link": get_full_url(link["href"])})

    final_links = season_based_links or highest_episode_links or links

    document = {
        "img_url": img_url,
        "links": final_links,
        "added_on": datetime.utcnow(),
    }

    await db.add_document(document)
    return document

async def start_processing():
    html = await fetch(BASE_URL)
    if not html:
        logging.warning("No content found on the main page!")
        return

    topic_links = await parse_links(html)
    for li_link in topic_links:
        logging.info(f"Processing: {li_link}")
        await fetch_attachments(li_link)

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_handler(request):
    return web.json_response({"status": "MadxBotz is live"})

async def web_server():
    app = web.Application(client_max_size=30000000)
    app.add_routes(routes)
    return app

User = Client(
    "User",
    session_string=USER_SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

async def ping_server():
    while True:
        try:
            await start_processing()
        except Exception as e:
            logging.error(f"Processing error: {e}")
        await asyncio.sleep(60)

async def ping_main_server():
    try:
        await User.start()
        logging.info("User Session started.")
        await User.send_message(GROUP_ID, "User Session Started")
    except Exception as e:
        logging.error(f"Failed to start User session: {e}")

    while True:
        await asyncio.sleep(250)
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.get(SERVER_URL) as resp:
                    logging.info(f"Ping response: {resp.status}")
        except Exception:
            logging.warning("Ping failed.")
            traceback.print_exc()

async def stop_user():
    await User.send_message(GROUP_ID, "User Session Stopped")
    await User.stop()
    logging.info("User Session Stopped.")
