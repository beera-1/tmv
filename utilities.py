import asyncio
import io
import logging
import re
import urllib.parse
import aiohttp
import cloudscraper
from bs4 import BeautifulSoup
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
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


async def fetch(url):
    scraper = cloudscraper.create_scraper()
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            executor, lambda: scraper.get(url, headers=HTTP_HEADERS, timeout=20)
        )
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logging.warning(f"Page not found (404): {url}")
        else:
            logging.error(f"HTTP error fetching {url}: {str(e)}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {str(e)}")
        return None


async def download_file_bytes(url):
    """Downloads raw torrent binary bytes to bypass Telegram WEBPAGE_MEDIA_EMPTY errors."""
    scraper = cloudscraper.create_scraper()
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            executor, lambda: scraper.get(url, headers=HTTP_HEADERS, timeout=25)
        )
        response.raise_for_status()
        return response.content
    except Exception as e:
        logging.error(f"Failed to download torrent binary from {url}: {e}")
        return None


def extract_media_size(text):
    """Extracts media size (e.g., '10GB', '7.2GB', '850MB') from filename."""
    if not text:
        return ""
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB))", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


async def resolve_cyberloom(start_url: str) -> dict:
    target_url = start_url.strip()
    timeout = aiohttp.ClientTimeout(total=35)

    async with aiohttp.ClientSession(headers=HTTP_HEADERS, timeout=timeout) as session:
        try:
            async with session.get(target_url, allow_redirects=True) as res1:
                html1 = await res1.text()
                current_url = str(res1.url)

            soup1 = BeautifulSoup(html1, "html.parser")
            cta = soup1.find("a", id="cta")

            if cta and cta.get("href"):
                next_url = cta["href"]
                await asyncio.sleep(1.5)
                async with session.get(next_url, allow_redirects=True) as res2:
                    html_target = await res2.text()
                    current_url = str(res2.url)
            else:
                html_target = html1

            match = re.search(r"var (?:link|hash)\s*=\s*'([^']+)'", html_target)
            if match:
                import base64
                destination_url = base64.b64decode(match.group(1)).decode("utf-8")
            else:
                soup_target = BeautifulSoup(html_target, "html.parser")
                cont_btn = soup_target.find("a", id="continue-btn")
                destination_url = cont_btn["href"] if (cont_btn and cont_btn.get("href")) else current_url

            final_html = ""
            landing_host_url = destination_url
            for attempt in range(3):
                async with session.get(destination_url, allow_redirects=True) as res3:
                    final_html = await res3.text()
                    landing_host_url = str(res3.url)
                if "download-grid" in final_html or "data-token" in final_html or "cdn." in final_html:
                    break
                await asyncio.sleep(1.5)

            soup3 = BeautifulSoup(final_html, "html.parser")
            parsed_host = urllib.parse.urlparse(landing_host_url)
            base_url = f"{parsed_host.scheme}://{parsed_host.netloc}"

            raw_title = soup3.find("h1").text.strip() if soup3.find("h1") else "Direct File"
            cleaned_title = re.sub(
                r"^www\.[a-zA-Z0-9-]+\.[a-z]+\s*[-_]*\s*", "", raw_title, flags=re.IGNORECASE
            ).strip(" -_")

            size_match = re.search(r"(\d+\.?\d*\s*(?:MB|GB|KB))", final_html)
            file_size = size_match.group(1) if size_match else "N/A"

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
                    for _ in range(3):
                        async with session.get(api_endpoint, headers=api_headers) as api_res:
                            if api_res.status == 200:
                                api_data = await api_res.json()
                                if api_data.get("success") and api_data.get("url"):
                                    raw_api_url = api_data["url"]
                                    final_download_url = (
                                        raw_api_url
                                        if raw_api_url.startswith("http")
                                        else f"{base_url}{raw_api_url}"
                                    )
                                    if api_data.get("t"):
                                        final_download_url += f"&t={api_data['t']}"
                                    break
                        await asyncio.sleep(1.5)
                elif "url=" in href:
                    final_download_url = urllib.parse.unquote(href.split("url=")[1].split("&")[0])
                elif href.startswith("http") and "messycloud" not in href:
                    final_download_url = href

                if final_download_url:
                    direct_links.append({"name": label, "link": final_download_url})

            return {
                "success": True,
                "original_url": start_url,
                "title": cleaned_title,
                "size": file_size,
                "links": direct_links,
            }

        except Exception as err:
            return {"success": False, "original_url": start_url, "error": str(err)}


async def parse_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/index.php?/forums/topic/" in href:
            # Filter real topic slugs (ignores navigation topic 183-0)
            if re.search(r"topic/\d{4,}-[a-zA-Z0-9-]+", href):
                if href not in links:
                    links.append(href)
            if len(links) == 20:
                break
    return links


async def fetch_attachments(page_url):
    # --- 1. DUPLICATE CHECK: Skip if topic already scraped and posted ---
    try:
        if await db.is_movie_present(page_url):
            logging.info(f"[SKIP] Page already processed: {page_url}")
            return None
    except Exception:
        pass

    html = await fetch(page_url)
    if not html:
        logging.warning(f"No content fetched from {page_url}, skipping.")
        return None

    domain_removal_regex = re.compile(r"^www\.[a-zA-Z0-9-]+\.[a-z]+\s*[-_]*\s*", re.IGNORECASE)
    mkv_torrent_removal_regex = re.compile(r"\.mkv\.torrent$", re.IGNORECASE)

    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.find("div", class_="cPost_contentWrap")
    img_url = None
    if content_div:
        img_tag = content_div.find("img")
        if img_tag and img_tag.get("src"):
            img_url = img_tag["src"]

    attachment_tags = [a for a in soup.find_all("a", href=True) if "attachment.php" in a["href"]]
    if not attachment_tags:
        return None

    parsed_entries = []

    for index, a_tag in enumerate(attachment_tags):
        link_href = a_tag["href"]
        link_text = a_tag.get_text(strip=True)

        clean_name = domain_removal_regex.sub("", link_text)
        clean_name = mkv_torrent_removal_regex.sub("", clean_name).strip(" -_")

        file_size = extract_media_size(clean_name)

        cyberloom_url = None
        next_dl = a_tag.find_next("a", href=re.compile(r"https?://(?:www\.)?(?:cyberloom|inkvoyage)\.[a-z]+/(?:l|out)\b"))

        if next_dl:
            if index + 1 < len(attachment_tags):
                next_attach = attachment_tags[index + 1]
                if next_dl.sourceline is None or next_attach.sourceline is None or next_dl.sourceline < next_attach.sourceline:
                    cyberloom_url = next_dl["href"]
            else:
                cyberloom_url = next_dl["href"]

        parsed_entries.append({
            "name": clean_name,
            "link": link_href,
            "torrent_link": link_href,
            "size": file_size,
            "cyberloom_url": cyberloom_url,
            "direct_links": []
        })

    # Resolve Cyberloom direct links
    bypass_tasks = []
    task_indices = []
    for i, entry in enumerate(parsed_entries):
        if entry["cyberloom_url"]:
            bypass_tasks.append(resolve_cyberloom(entry["cyberloom_url"]))
            task_indices.append(i)

    if bypass_tasks:
        results = await asyncio.gather(*bypass_tasks)
        for idx, res in zip(task_indices, results):
            if res.get("success") and res.get("links"):
                parsed_entries[idx]["direct_links"] = res["links"]

    # Send each torrent document to the Telegram channel only once
    for entry in parsed_entries:
        filename = f"@AddaFileZ_{entry['name'].replace(' ', '_')}.torrent"
        size_display = f" [{entry['size']}]" if entry['size'] else ""
        caption = f"<b>@AddaFileZ {entry['name']}{size_display}</b>"

        buttons = []
        for dl in entry["direct_links"]:
            buttons.append([InlineKeyboardButton(f"⚡ Direct Link ({dl['name']})", url=dl["link"])])

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        caption += "\n\n<b>〽️ Powered by @AddaFileZ</b>"

        try:
            torrent_bytes = await download_file_bytes(entry["torrent_link"])
            if torrent_bytes:
                file_io = io.BytesIO(torrent_bytes)
                file_io.name = filename

                await User.send_document(
                    chat_id=GROUP_ID,
                    document=file_io,
                    file_name=filename,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"Error sending document: {e}")

    # Standardize links field and store in database
    db_links = [
        {
            "name": entry["name"],
            "link": entry["link"],
            "direct_links": entry["direct_links"]
        }
        for entry in parsed_entries
    ]

    document = {
        "page_url": page_url,
        "img_url": img_url,
        "links": db_links,
        "added_on": datetime.utcnow(),
    }

    await db.add_document(document)
    return document


async def start_processing():
    main_page_html = await fetch(BASE_URL)
    if main_page_html:
        fetched_links = await parse_links(main_page_html)
        for li_link in fetched_links:
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
        # Check every 2 minutes instead of 60 seconds to respect server resources
        await asyncio.sleep(120)


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
                    logging.info(f"Pinged server with response: {resp.status}")
        except TimeoutError:
            logging.warning("Couldn't connect to the site URL.")
        except Exception:
            traceback.print_exc()


async def stop_user():
    await User.send_message(GROUP_ID, "User Session Stopped")
    await User.stop()
    logging.info("User Session Stopped.")
