import asyncio
import io
import logging
import aiohttp
import cloudscraper
from bs4 import BeautifulSoup
import re
import base64
import urllib.parse
from datetime import datetime
from database import db
from configs import *
from aiohttp import web
from pyrogram import enums, Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor

message_lock = asyncio.Lock()
executor = ThreadPoolExecutor()

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

async def fetch(url):
    scraper = cloudscraper.create_scraper()
    headers = HTTP_HEADERS
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            executor, lambda: scraper.get(url, headers=headers, timeout=20)
        )
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logging.warning(f"Page not found (404): {url}")
        else:
            logging.error(f"HTTP error fetching {url}: {str(e)}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {str(e)}")
        return None

async def fetch_bytes(url):
    """Downloads raw binary file bytes (.torrent) to upload to Telegram."""
    scraper = cloudscraper.create_scraper()
    headers = HTTP_HEADERS
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            executor, lambda: scraper.get(url, headers=headers, timeout=30)
        )
        response.raise_for_status()
        return response.content
    except Exception as e:
        logging.error(f"Error fetching binary file {url}: {str(e)}")
        return None


# -----------------------------------------------------------
# SIZE EXTRACTION HELPERS
# -----------------------------------------------------------
def extract_media_size(text):
    """Extracts human-readable size (e.g. 1.6GB, 700MB) for captions."""
    if not text:
        return ""
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB))", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def get_size_in_bytes(text):
    """Extracts raw size in bytes for threshold filtering."""
    if not text:
        return None

    text = text.lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb)", text)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)

    if unit == "gb":
        return int(value * 1024 * 1024 * 1024)
    else:
        return int(value * 1024 * 1024)


# -----------------------------------------------------------
# CYBERLOOM / MESSYCLOUD BYPASS ENGINE
# -----------------------------------------------------------
async def resolve_cyberloom(start_url: str) -> dict:
    target_url = start_url.strip()
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(headers=HTTP_HEADERS, timeout=timeout) as session:
        try:
            async with session.get(target_url, allow_redirects=True) as res1:
                html1 = await res1.text()

            soup1 = BeautifulSoup(html1, "html.parser")
            cta = soup1.find("a", id="cta")

            if cta and cta.get("href"):
                next_url = cta["href"]
                async with session.get(next_url, allow_redirects=True) as res2:
                    html2 = await res2.text()
            else:
                html2 = html1
                next_url = target_url

            match = re.search(r"var (?:link|hash)\s*=\s*'([^']+)'", html2)
            if match:
                decoded_url = base64.b64decode(match.group(1)).decode("utf-8")
            else:
                soup2 = BeautifulSoup(html2, "html.parser")
                cont_btn = soup2.find("a", id="continue-btn")
                decoded_url = cont_btn["href"] if (cont_btn and cont_btn.get("href")) else next_url

            async with session.get(decoded_url, allow_redirects=True) as res3:
                final_html = await res3.text()
                landing_host_url = str(res3.url)

            soup3 = BeautifulSoup(final_html, "html.parser")
            parsed_host = urllib.parse.urlparse(landing_host_url)
            base_url = f"{parsed_host.scheme}://{parsed_host.netloc}"

            direct_links = []
            for a_tag in soup3.find_all("a"):
                label = a_tag.get_text(strip=True)
                token = a_tag.get("data-token")
                href = a_tag.get("href", "")

                if not label or any(x in label.lower() for x in ["login", "home", "back", "messycloud"]):
                    continue

                final_download_url = None
                if token:
                    api_endpoint = f"{base_url}/api/link/{token}"
                    api_headers = {
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": landing_host_url,
                    }
                    async with session.get(api_endpoint, headers=api_headers) as api_res:
                        if api_res.status == 200:
                            api_data = await api_res.json()
                            if isinstance(api_data, dict) and api_data.get("success") and api_data.get("url"):
                                raw_api_url = api_data["url"]
                                final_download_url = (
                                    raw_api_url
                                    if raw_api_url.startswith("http")
                                    else f"{base_url}{raw_api_url}"
                                )
                                if api_data.get("t"):
                                    final_download_url += f"&t={api_data['t']}"
                elif "url=" in href:
                    final_download_url = urllib.parse.unquote(href.split("url=")[1].split("&")[0])
                elif href.startswith("http") and "messycloud" not in href:
                    final_download_url = href

                if final_download_url:
                    direct_links.append({"name": label, "link": final_download_url})

            return {
                "success": True,
                "links": direct_links,
            }

        except Exception as err:
            return {"success": False, "error": str(err)}


# -----------------------------------------------------------
# TOPIC PARSER & AUTO-POSTER
# -----------------------------------------------------------
async def parse_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Skip pagination / invalid topic routes and only parse full topic URLs
        if "/index.php?/forums/topic/" in href and re.search(r"/topic/\d+-[a-zA-Z0-9_-]+", href):
            if href not in links:
                links.append(href)
            if len(links) == 20:
                break
    return links


async def fetch_attachments(page_url):
    html = await fetch(page_url)
    if not html:
        logging.warning(f"No content fetched from {page_url}, skipping.")
        return None

    episode_pattern = re.compile(r"E(?:P)?(\d{1,2})", re.IGNORECASE)
    non_episode_regex = re.compile(r"S(\d{1,2})\s*(?:E|EP)?\s*\(?(\d+(?:-\d+))\)?", re.IGNORECASE)
    domain_removal_regex = re.compile(r"^www\.[a-zA-Z0-9-]+\.[a-z]+\s*[-_]*\s*", re.IGNORECASE)
    mkv_torrent_removal_regex = re.compile(r"\.mkv\.torrent$", re.IGNORECASE)

    soup = BeautifulSoup(html, "html.parser")
    
    # Extract Poster Image
    content_div = soup.find("div", class_="cPost_contentWrap")
    img_url = None
    if content_div:
        img_tag = content_div.find("img")
        if img_tag and img_tag.get("src"):
            img_url = img_tag["src"]

    attachment_tags = [a for a in soup.find_all("a", href=True) if "attachment.php" in a["href"]]

    highest_episode_number = 0
    highest_episode_links = []
    season_based_links = []
    general_links = []
    highest_season = 0
    highest_episode_range = (0, 0)

    for index, a_tag in enumerate(attachment_tags):
        link_href = a_tag["href"]
        link_text = a_tag.get_text(strip=True)

        size_tag = a_tag.find_next("span", string=re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB)", re.I))
        size_text = size_tag.text if size_tag else link_text
        size_in_bytes = get_size_in_bytes(size_text)

        clean_name = domain_removal_regex.sub("", link_text)
        clean_name = mkv_torrent_removal_regex.sub("", clean_name).strip(" -_")
        human_size = extract_media_size(size_text) or extract_media_size(clean_name)

        if size_in_bytes is None:
            continue

        # Look for corresponding Cyberloom direct link button
        cyberloom_url = None
        next_dl = a_tag.find_next("a", href=re.compile(r"https?://(?:www\.)?cyberloom\.[a-z]+/l/\w+"))
        if next_dl:
            if index + 1 < len(attachment_tags):
                next_attach = attachment_tags[index + 1]
                if next_dl.sourceline is None or next_attach.sourceline is None or next_dl.sourceline < next_attach.sourceline:
                    cyberloom_url = next_dl["href"]
            else:
                cyberloom_url = next_dl["href"]

        entry = {
            "name": clean_name,
            "link": link_href,
            "size": human_size,
            "cyberloom_url": cyberloom_url,
            "direct_links": []
        }

        # Season-based parsing
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
                season_based_links = [entry]
            elif season_number == highest_season and episode_start <= highest_episode_range[1]:
                season_based_links.append(entry)

        # Episode-based parsing
        episode_matches = episode_pattern.findall(link_text)
        if episode_matches:
            current_episode_number = max(int(ep) for ep in episode_matches)
            if size_in_bytes < 4 * 1024 * 1024 * 1024:
                if current_episode_number > highest_episode_number:
                    highest_episode_number = current_episode_number
                    highest_episode_links = [entry]
                elif current_episode_number == highest_episode_number:
                    highest_episode_links.append(entry)
        else:
            if size_in_bytes < 4 * 1024 * 1024 * 1024:
                general_links.append(entry)

    parsed_entries = (
        season_based_links
        if season_based_links
        else (highest_episode_links if highest_episode_links else general_links)
    )

    # Concurrently resolve Cyberloom links
    bypass_tasks = []
    task_indices = []
    for i, entry in enumerate(parsed_entries):
        if entry.get("cyberloom_url"):
            bypass_tasks.append(resolve_cyberloom(entry["cyberloom_url"]))
            task_indices.append(i)

    if bypass_tasks:
        results = await asyncio.gather(*bypass_tasks)
        for idx, res in zip(task_indices, results):
            if res and res.get("success") and res.get("links"):
                parsed_entries[idx]["direct_links"] = res["links"]

    # Post to Telegram
    for entry in parsed_entries:
        filename = f"@AddaFileZ_{entry.get('name', 'file').replace(' ', '_')}.torrent"
        size_display = f" [{entry['size']}]" if entry.get('size') else ""
        caption = f"<b>@AddaFileZ {entry.get('name', '')}{size_display}</b>"

        buttons = []
        for dl in entry.get("direct_links", []):
            dl_name = dl.get("name", "Download")
            dl_link = dl.get("link")
            if dl_link:
                buttons.append([InlineKeyboardButton(f"⚡ Direct Link ({dl_name})", url=dl_link)])

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        caption += "\n\n<b>〽️ Powered by @AddaFileZ</b>"

        try:
            torrent_raw = await fetch_bytes(entry["link"])
            if torrent_raw:
                torrent_stream = io.BytesIO(torrent_raw)
                torrent_stream.name = filename

                await User.send_document(
                    chat_id=GROUP_ID,
                    document=torrent_stream,
                    file_name=filename,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(2)
            else:
                logging.warning(f"Could not fetch torrent file for: {entry.get('name')}")
        except Exception as e:
            logging.error(f"Error sending document: {e}")

    document = {
        "img_url": img_url,
        "page_url": page_url,
        "links": parsed_entries,
        "added_on": datetime.utcnow(),
    }
    await db.add_document(document)
    return document


async def start_processing():
    main_page_html = await fetch(BASE_URL)
    if main_page_html:
        fetched_links = await parse_links(main_page_html)
        for li_link in fetched_links:
            logging.info(f"Fetching attachments from {li_link}")
            await fetch_attachments(li_link)
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
    "User", session_string=USER_SESSION_STRING, api_hash=API_HASH, api_id=API_ID
)


async def ping_server():
    while True:
        try:
            await start_processing()
        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
        await asyncio.sleep(60)


async def ping_main_server():
    try:
        await User.start()
        logging.info("User Session started.")
        await User.send_message(GROUP_ID, "User Session Started")
    except Exception as e:
        logging.error(f"Error Starting User: {str(e)}")

    while True:
        await asyncio.sleep(250)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(SERVER_URL) as resp:
                    logging.info("Pinged server with response: {}".format(resp.status))
        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            logging.warning("Couldn't connect to the site URL.")
        except Exception:
            traceback.print_exc()


async def stop_user():
    await User.send_message(GROUP_ID, "User Session Stopped")
    await User.stop()
    logging.info("User Session Stopped.")
