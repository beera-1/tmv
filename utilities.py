import asyncio
import logging
import re
import traceback
from datetime import datetime
from urllib.parse import urlparse, urljoin

import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
import cloudscraper
from pyrogram import Client

from configs import *
from database import db


# ============================================================
# GLOBALS & CONSTANTS
# ============================================================

scraper = cloudscraper.create_scraper(delay=10, browser="chrome")

# Strictly matches valid forum release threads (5+ digit IDs)
# Explicitly rejects index/pagination links such as '183-0/'
TOPIC_REGEX = re.compile(r"/index\.php\?/forums/topic/([1-9]\d{4,})-([^/]+)", re.IGNORECASE)


# ============================================================
# UTILITIES & CATEGORIZATION
# ============================================================

def fix_url(href: str) -> str:
    return href if href.startswith("http") else urljoin(BASE_URL, href)


def categorize_content(title: str) -> str:
    t = title.lower()
    series_patterns = [r"s\d{1,2}", r"ep\s?\d+", r"episode", r"season", r"complete"]

    if any(re.search(p, t) for p in series_patterns) or "web series" in t or "tv show" in t:
        return "Series"
    if "dubbed" in t or "tam+" in t or "multi" in t:
        return "Dubbed"
    return "Movies"


def get_size_in_bytes(text):
    if not text:
        return None

    text = str(text).lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb)", text)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)
    if unit == "gb":
        return int(value * 1024 * 1024 * 1024)
    return int(value * 1024 * 1024)


# ============================================================
# FETCH PAGE
# ============================================================

async def fetch(url):
    try:
        response = await asyncio.to_thread(scraper.get, url, timeout=30)
        if response.status_code == 404:
            logging.warning(f"Page not found (404): {url}")
            return None
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return None


# ============================================================
# PARSE TOPIC LINKS
# ============================================================

async def parse_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if TOPIC_REGEX.search(href):
            clean_href = fix_url(href.split("&")[0].rstrip("/"))
            if clean_href not in links:
                links.append(clean_href)
            if len(links) == 20:
                break

    return links


# ============================================================
# FETCH ATTACHMENTS
# ============================================================

async def fetch_attachments(page_url):
    html = await fetch(page_url)
    if not html:
        return None

    episode_pattern = re.compile(r"E(?:P)?(\d{1,2})", re.IGNORECASE)
    non_episode_regex = re.compile(r"S(\d{1,2})\s*(?:E|EP)?\s*\(?(\d+(?:-\d+))\)?", re.IGNORECASE)
    tamilmv_domain_regex = re.compile(r"www\.1TamilMV\.[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*", re.IGNORECASE)

    soup = BeautifulSoup(html, "html.parser")
    all_qualities = []

    content_div = soup.find("div", class_="cPost_contentWrap")
    img_url = None
    if content_div:
        img_tag = content_div.find("img")
        if img_tag and img_tag.get("src"):
            img_url = img_tag.get("src")

    highest_episode_number = 0
    highest_episode_links = []
    season_based_links = []
    highest_season = 0
    highest_episode_range = (0, 0)

    search_scope = content_div if content_div else soup

    for link in search_scope.find_all("a", href=True):
        href = link["href"]
        if "attachment.php" not in href:
            continue

        link_text = link.get_text(strip=True)
        if not link_text:
            continue

        size_in_bytes = None
        for sib in link.find_all_next(string=True, limit=6):
            if re.search(r"\d+(?:\.\d+)?\s*(?:GB|MB)", str(sib), re.I):
                size_in_bytes = get_size_in_bytes(str(sib))
                break

        if not size_in_bytes:
            size_in_bytes = get_size_in_bytes(link_text)

        clean_link_text = tamilmv_domain_regex.sub("", link_text, count=1)
        clean_link_text = re.sub(r"\s*-\s*-\s*", " - ", clean_link_text, count=1).strip()
        clean_link_text = re.sub(r"^[\s\-_]+", "", clean_link_text).strip()

        item = {
            "name": clean_link_text,
            "link": fix_url(href),
            "size_bytes": size_in_bytes,
            "category": categorize_content(clean_link_text)
        }

        # TV Show Season Batch
        season_match = non_episode_regex.search(link_text)
        if season_match:
            season_number = int(season_match.group(1))
            episode_range = season_match.group(2)
            if "-" in episode_range:
                episode_start, episode_end = map(int, episode_range.split("-"))
            else:
                episode_start = episode_end = int(episode_range)

            if season_number > highest_season or (
                season_number == highest_season and episode_end > highest_episode_range[1]
            ):
                highest_season = season_number
                highest_episode_range = (episode_start, episode_end)
                season_based_links = [item]
            elif season_number == highest_season and episode_start <= highest_episode_range[1]:
                season_based_links.append(item)
            continue

        # TV Show Single Episode
        episode_matches = episode_pattern.findall(link_text)
        if episode_matches:
            current_episode_number = max(int(ep) for ep in episode_matches)
            if current_episode_number > highest_episode_number:
                highest_episode_number = current_episode_number
                highest_episode_links = [item]
            elif current_episode_number == highest_episode_number:
                highest_episode_links.append(item)
            continue

        all_qualities.append(item)

    if season_based_links:
        final_links = season_based_links
    elif highest_episode_links:
        final_links = highest_episode_links
    else:
        final_links = all_qualities

    if not final_links:
        return None

    document = {
        "page_url": page_url,
        "img_url": img_url,
        "links": final_links,
        "added_on": datetime.utcnow(),
    }

    await db.add_document(document)
    return document


# ============================================================
# MAIN LOOP & SERVER
# ============================================================

async def start_processing():
    main_page_html = await fetch(BASE_URL)
    if main_page_html:
        fetched_links = await parse_links(main_page_html)
        for li_link in fetched_links:
            logging.info(f"Fetching attachments from {li_link}")
            try:
                await fetch_attachments(li_link)
            except Exception as e:
                logging.error(f"Error processing {li_link}: {e}", exc_info=True)
            await asyncio.sleep(2)
    else:
        logging.warning("No content found on the main page!")


routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response("MadxBotz")


async def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    return web_app


User = Client(
    "User",
    session_string=USER_SESSION_STRING,
    api_hash=API_HASH,
    api_id=API_ID
)


async def ping_server():
    while True:
        try:
            await start_processing()
        except Exception as e:
            logging.error(f"Unexpected error: {e}", exc_info=True)
        await asyncio.sleep(60)


async def ping_main_server():
    try:
        if not User.is_connected:
            await User.start()
        logging.info("User Session started.")
        await User.send_message(GROUP_ID, "User Session Started")
    except Exception as e:
        logging.error(f"Error Starting User: {e}", exc_info=True)

    while True:
        await asyncio.sleep(250)
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(SERVER_URL) as resp:
                    logging.info(f"Pinged server with response: {resp.status}")
        except (asyncio.TimeoutError, TimeoutError):
            logging.warning("Couldn't connect to the site URL.")
        except Exception:
            traceback.print_exc()


async def stop_user():
    try:
        if User.is_connected:
            await User.send_message(GROUP_ID, "User Session Stopped")
            await User.stop()
            logging.info("User Session Stopped")
    except Exception as e:
        logging.error(f"Error stopping User: {e}", exc_info=True)
