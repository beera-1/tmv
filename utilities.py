import asyncio
import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
import cloudscraper
from pyrogram import Client, enums
import requests

from configs import *
from database import db


# ============================================================
# GLOBALS
# ============================================================

message_lock = asyncio.Lock()
executor = ThreadPoolExecutor()


# ============================================================
# FETCH PAGE
# ============================================================

async def fetch(url):
    scraper = cloudscraper.create_scraper()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/113.0.0.0 Safari/537.36"
        )
    }

    loop = asyncio.get_event_loop()

    try:
        response = await loop.run_in_executor(
            executor,
            lambda: scraper.get(
                url,
                headers=headers,
                timeout=30
            )
        )

        response.raise_for_status()

        return response.text

    except requests.exceptions.HTTPError as e:

        if e.response is not None and e.response.status_code == 404:
            logging.warning(
                f"Page not found (404): {url}"
            )
        else:
            logging.error(
                f"HTTP error fetching {url}: {e}"
            )

        return None

    except requests.exceptions.RequestException as e:

        logging.error(
            f"Error fetching {url}: {e}"
        )

        return None

    except Exception as e:

        logging.error(
            f"Unexpected error fetching {url}: {e}",
            exc_info=True
        )

        return None


# ============================================================
# SIZE PARSER
# ============================================================

def get_size_in_bytes(text):

    if not text:
        return None

    text = str(text).lower()

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(gb|mb)",
        text
    )

    if not match:
        return None

    value = float(
        match.group(1)
    )

    unit = match.group(2)

    if unit == "gb":

        return int(
            value * 1024 * 1024 * 1024
        )

    return int(
        value * 1024 * 1024
    )


# ============================================================
# PARSE TOPIC LINKS
# ============================================================

async def parse_links(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link["href"]

        if "/index.php?/forums/topic/" in href:

            if href not in links:

                links.append(href)

            if len(links) == 20:
                break

    return links


# ============================================================
# FETCH ATTACHMENTS
# ============================================================

async def fetch_attachments(page_url):

    html = await fetch(
        page_url
    )

    if not html:

        logging.warning(
            f"No content fetched from "
            f"{page_url}, skipping."
        )

        return None


    # ========================================================
    # EPISODE REGEX
    # ========================================================

    episode_pattern = re.compile(
        r"E(?:P)?(\d{1,2})",
        re.IGNORECASE
    )


    # ========================================================
    # SEASON / EPISODE RANGE REGEX
    # ========================================================

    non_episode_regex = re.compile(
        r"S(\d{1,2})\s*"
        r"(?:E|EP)?\s*"
        r"\(?(\d+(?:-\d+))\)?",
        re.IGNORECASE
    )


    # ========================================================
    # DOMAIN REMOVAL
    # ========================================================

    domain_removal_regex = re.compile(
        r"\b(?:www\.)?"
        r"[a-zA-Z0-9.-]+\."
        r"[a-zA-Z]{2,6}\b"
    )


    # ========================================================
    # IMPORTANT:
    #
    # DO NOT REMOVE .mkv.torrent HERE.
    #
    # Your __init__.py handles the final filename/caption.
    # ========================================================


    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    all_qualities = []


    # ========================================================
    # FIND POST IMAGE
    # ========================================================

    content_div = soup.find(
        "div",
        class_="cPost_contentWrap"
    )

    img_url = None

    if content_div:

        img_tag = content_div.find(
            "img"
        )

        if img_tag and img_tag.get("src"):

            img_url = img_tag.get(
                "src"
            )


    # ========================================================
    # EPISODE TRACKING
    # ========================================================

    highest_episode_number = 0

    highest_episode_links = []


    # ========================================================
    # SEASON TRACKING
    # ========================================================

    season_based_links = []

    highest_season = 0

    highest_episode_range = (
        0,
        0
    )


    # ========================================================
    # PROCESS ALL ATTACHMENTS
    # ========================================================

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link["href"]

        # Only torrent attachments
        if "attachment.php" not in href:
            continue


        # ====================================================
        # ORIGINAL LINK TEXT
        # ====================================================

        link_text = link.get_text(
            strip=True
        )

        if not link_text:
            continue


        # ====================================================
        # FIND FILE SIZE
        # ====================================================

        size_tag = link.find_next(
            "span",
            string=re.compile(
                r"\d+(?:\.\d+)?\s*"
                r"(?:GB|MB)",
                re.I
            )
        )

        size_text = (
            size_tag.text
            if size_tag
            else link_text
        )

        size_in_bytes = get_size_in_bytes(
            size_text
        )


        # ====================================================
        # CLEAN DOMAIN ONLY
        #
        # DO NOT REMOVE:
        # @AddaFileZ
        # .mkv
        # .torrent
        # ====================================================

        clean_link_text = domain_removal_regex.sub(
            "",
            link_text
        )

        clean_link_text = clean_link_text.strip(
            " -_"
        )


        # ====================================================
        # STORE ATTACHMENT
        # ====================================================

        item = {
            "name": clean_link_text,
            "link": href,
            "size_bytes": size_in_bytes,
        }


        # ====================================================
        # TV SHOW SEASON BATCH
        # ====================================================

        season_match = non_episode_regex.search(
            link_text
        )

        if season_match:

            season_number = int(
                season_match.group(1)
            )

            episode_range = (
                season_match.group(2)
            )


            if "-" in episode_range:

                episode_start, episode_end = map(
                    int,
                    episode_range.split("-")
                )

            else:

                episode_start = (
                    episode_end
                ) = int(
                    episode_range
                )


            # ================================================
            # NEW HIGHEST SEASON / EPISODE
            # ================================================

            if (
                season_number > highest_season
                or (
                    season_number == highest_season
                    and episode_end
                    > highest_episode_range[1]
                )
            ):

                highest_season = (
                    season_number
                )

                highest_episode_range = (
                    episode_start,
                    episode_end
                )

                season_based_links = [
                    item
                ]


            # ================================================
            # SAME SEASON
            # ================================================

            elif (
                season_number == highest_season
                and episode_start
                <= highest_episode_range[1]
            ):

                season_based_links.append(
                    item
                )


            continue


        # ====================================================
        # TV SHOW SINGLE EPISODE
        # ====================================================

        episode_matches = episode_pattern.findall(
            link_text
        )

        if episode_matches:

            current_episode_number = max(
                int(ep)
                for ep in episode_matches
            )


            # ================================================
            # NEW HIGHEST EPISODE
            # ================================================

            if (
                current_episode_number
                > highest_episode_number
            ):

                highest_episode_number = (
                    current_episode_number
                )

                highest_episode_links = [
                    item
                ]


            # ================================================
            # SAME EPISODE
            # ================================================

            elif (
                current_episode_number
                == highest_episode_number
            ):

                highest_episode_links.append(
                    item
                )


            continue


        # ====================================================
        # MOVIE QUALITY FILE
        # ====================================================

        all_qualities.append(
            item
        )


    # ========================================================
    # SELECT FINAL LINKS
    # ========================================================

    if season_based_links:

        final_links = (
            season_based_links
        )

    elif highest_episode_links:

        final_links = (
            highest_episode_links
        )

    else:

        final_links = (
            all_qualities
        )


    # ========================================================
    # CREATE DOCUMENT
    # ========================================================

    document = {
        "img_url": img_url,
        "links": final_links,
        "added_on": datetime.utcnow(),
    }


    # ========================================================
    # SAVE TO DATABASE
    # ========================================================

    await db.add_document(
        document
    )

    return document


# ============================================================
# START PROCESSING
# ============================================================

async def start_processing():

    main_page_html = await fetch(
        BASE_URL
    )

    if main_page_html:

        fetched_links = await parse_links(
            main_page_html
        )

        for li_link in fetched_links:

            logging.info(
                f"Fetching attachments from "
                f"{li_link}"
            )

            try:

                await fetch_attachments(
                    li_link
                )

            except Exception as e:

                logging.error(
                    f"Error processing "
                    f"{li_link}: {e}",
                    exc_info=True
                )

    else:

        logging.warning(
            "No content found on the main page!"
        )


# ============================================================
# WEB ROUTES
# ============================================================

routes = web.RouteTableDef()


@routes.get(
    "/",
    allow_head=True
)
async def root_route_handler(request):

    return web.json_response(
        "MadxBotz"
    )


# ============================================================
# WEB SERVER
# ============================================================

async def web_server():

    web_app = web.Application(
        client_max_size=30000000
    )

    web_app.add_routes(
        routes
    )

    return web_app


# ============================================================
# USER CLIENT
# ============================================================

User = Client(
    "User",
    session_string=USER_SESSION_STRING,
    api_hash=API_HASH,
    api_id=API_ID
)


# ============================================================
# PING / SCRAPE LOOP
# ============================================================

async def ping_server():

    while True:

        try:

            await start_processing()

        except Exception as e:

            logging.error(
                f"Unexpected error: {e}",
                exc_info=True
            )

        await asyncio.sleep(
            60
        )


# ============================================================
# MAIN SERVER PING
# ============================================================

async def ping_main_server():

    try:

        await User.start()

        logging.info(
            "User Session started."
        )

        await User.send_message(
            GROUP_ID,
            "User Session Started"
        )

    except Exception as e:

        logging.error(
            f"Error Starting User: {e}",
            exc_info=True
        )


    while True:

        await asyncio.sleep(
            250
        )

        try:

            timeout = aiohttp.ClientTimeout(
                total=10
            )

            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                async with session.get(
                    SERVER_URL
                ) as resp:

                    logging.info(
                        f"Pinged server with response: "
                        f"{resp.status}"
                    )

        except asyncio.TimeoutError:

            logging.warning(
                "Couldn't connect to the site URL."
            )

        except TimeoutError:

            logging.warning(
                "Couldn't connect to the site URL."
            )

        except Exception:

            traceback.print_exc()


# ============================================================
# STOP USER
# ============================================================

async def stop_user():

    try:

        await User.send_message(
            GROUP_ID,
            "User Session Stopped"
        )

    except Exception as e:

        logging.warning(
            f"Could not send stop message: {e}"
        )

    try:

        await User.stop()

        logging.info(
            "User Session Stopped"
        )

    except Exception as e:

        logging.error(
            f"Error stopping User: {e}",
            exc_info=True
        )
