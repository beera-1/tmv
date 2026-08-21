import asyncio
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(executor, lambda: scraper.get(url, headers=headers))
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

            raw_title = soup3.find("h1").text.strip() if soup3.find("h1") else "Direct File"
            cleaned_title = re.sub(
                r"^www\.[a-zA-Z0-9-]+\.[a-z]+\s*[-_]*\s*", "", raw_title, flags=re.IGNORECASE
            ).strip(" -_")

            size_match = re.search(r"(\d+\.?\d*\s*(?:MB|GB|KB))", final_html)
            file_size = size_match.group(1) if size_match else "Unknown Size"

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
                            if api_data.get("success") and api_data.get("url"):
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
                "original_url": start_url,
                "title": cleaned_title,
                "size": file_size,
                "links": direct_links,
            }

        except Exception as err:
            return {"success": False, "original_url": start_url, "error": str(err)}


# -----------------------------------------------------------
# TOPIC PARSER
# -----------------------------------------------------------
async def parse_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        if "/index.php?/forums/topic/" in link["href"]:
            if link["href"] not in links:
                links.append(link["href"])
            if len(links) == 20:
                break
    return links

async def fetch_attachments(page_url):
    html = await fetch(page_url)
    if not html:
        logging.warning(f"No content fetched from {page_url}, skipping.")
        return None

    domain_removal_regex = re.compile(r"^www\.[a-zA-Z0-9-]+\.[a-z]+\s*[-_]*\s*", re.IGNORECASE)

    soup = BeautifulSoup(html, "html.parser")
    parsed_entries = []

    content_div = soup.find("div", class_="cPost_contentWrap")
    img_url = None
    if content_div:
        img_tag = content_div.find("img")
        if img_tag and img_tag.get("src"):
            img_url = img_tag["src"]

    # 1. Locate all attachment links
    attachment_tags = [a for a in soup.find_all("a", href=True) if "attachment.php" in a["href"]]

    for index, a_tag in enumerate(attachment_tags):
        link_href = a_tag["href"]
        link_text = a_tag.get_text(strip=True)

        # Remove site branding from the front while keeping the full filename intact
        clean_name = domain_removal_regex.sub("", link_text).strip(" -_")

        # 2. Extract corresponding magnet link if present
        magnet_href = None
        next_magnet = a_tag.find_next("a", href=re.compile(r"^magnet:\?"))
        if next_magnet:
            magnet_href = next_magnet["href"]

        # 3. Locate corresponding Cyberloom direct link below the attachment
        cyberloom_url = None
        next_dl = a_tag.find_next("a", href=re.compile(r"https?://(?:www\.)?cyberloom\.[a-z]+/l/\w+"))
        
        if next_dl:
            if index + 1 < len(attachment_tags):
                next_attach = attachment_tags[index + 1]
                if next_dl.sourceline is None or next_attach.sourceline is None or next_dl.sourceline < next_attach.sourceline:
                    cyberloom_url = next_dl["href"]
            else:
                cyberloom_url = next_dl["href"]

        parsed_entries.append({
            "name": clean_name,
            "torrent_link": link_href,
            "magnet": magnet_href,
            "cyberloom_url": cyberloom_url,
            "direct_links": []
        })

    # 4. Concurrently bypass paired Cyberloom links
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

    # 5. Build clean database documents without extra size brackets
    final_torrent_links = []
    for entry in parsed_entries:
        final_torrent_links.append({
            "name": entry["name"],
            "link": entry["torrent_link"],
            "magnet": entry["magnet"],
            "direct_links": entry["direct_links"]
        })

    document = {
        "img_url": img_url,
        "links": final_torrent_links,
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
        except TimeoutError:
            logging.warning("Couldn't connect to the site URL.")
        except Exception:
            traceback.print_exc()


async def stop_user():
    await User.send_message(GROUP_ID, "User Session Stopped")
    await User.stop()
    logging.info("User Session Stopped.")
