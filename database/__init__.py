import os
import re
import logging
import asyncio
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession, ClientTimeout
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client
import cloudscraper

from configs import *

executor = ThreadPoolExecutor()
os.makedirs("downloads", exist_ok=True)

User = Client(
    "User", session_string=USER_SESSION_STRING, api_hash=API_HASH, api_id=API_ID
)

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

def sync_fetch(url):
    try:
        response = scraper.get(url, timeout=15)
        response.raise_for_status()
        return response, int(response.headers.get("Content-Length", 0))
    except Exception as e:
        logging.error(f"[sync_fetch] Error: {url} => {e}")
        return None, 0

async def fetch(url):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, sync_fetch, url)

async def is_valid_link(url):
    response, _ = await fetch(url)
    return response is not None

async def download_file(url, local_filename):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response, expected_size = await fetch(url)
            if response:
                with open(local_filename, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                if os.path.getsize(local_filename) == expected_size:
                    logging.info(f"Downloaded {local_filename} successfully.")
                    return True
                else:
                    logging.error(f"Size mismatch: {url} (Attempt {attempt + 1})")
                    os.remove(local_filename)
            else:
                logging.error(f"Fetch failed: {url} (Attempt {attempt + 1})")
        except Exception as e:
            logging.error(f"Download failed: {url} => {e} (Attempt {attempt + 1})")
        await asyncio.sleep(1)
    return False

async def send_new_link_notification(links):
    async with User:
        if not links:
            await User.send_message(chat_id=GROUP_ID, text="Empty Array")
            return

        for link in links:
            filename = f"downloads/@ADDAFILES {link['name']}.torrent"
            full_url = link["link"] if link["link"].startswith("http") else urljoin(BASE_URL, link["link"])

            if await is_valid_link(full_url):
                if await download_file(full_url, filename):
                    try:
                        caption = f"""<b>@ADDAFILES {link['name']}\n\n<blockquote>〽️ Powered by @ADDAFILES</blockquote></b>"""

                        msg = await User.send_document(
                            chat_id=GROUP_ID,
                            document=filename,
                            thumb="database/thumb.jpg",
                            caption=caption,
                        )

                        await User.send_message(
                            chat_id=GROUP_ID,
                            text="/qbleech",
                            reply_to_message_id=msg.id,
                        )

                        await User.send_document(
                            chat_id=RSS_CHAT,
                            document=filename,
                            thumb="database/thumb.jpg",
                            caption=caption,
                        )
                    except Exception as e:
                        logging.error(f"Telegram send error: {link['link']} => {e}")
                    finally:
                        if os.path.exists(filename):
                            os.remove(filename)
                else:
                    logging.warning(f"Failed to download: {link['link']}")
            else:
                logging.warning(f"Invalid link: {link['link']}")

class Database:
    def __init__(self, url, db_name):
        self.db = AsyncIOMotorClient(url)[db_name]
        self.users_coll = self.db.users
        self.links_coll = self.db.attachments

    async def add_user(self, id):
        if not await self.is_present(id):
            await self.users_coll.insert_one(dict(id=id))

    async def is_present(self, id):
        return bool(await self.users_coll.find_one({"id": int(id)}))

    async def total_users(self):
        return await self.users_coll.count_documents({})

    async def count_all_links(self):
        return await self.links_coll.count_documents({})

    async def search_movie(self, movie_name):
        search_query = {
            "name": {
                "$regex": re.escape(movie_name).replace(r"\ ", r".*"),
                "$options": "i",
            }
        }
        return await self.links_coll.find(search_query).to_list(None)

    async def get_last_documents(self, count):
        return (
            await self.links_coll.find()
            .sort("added_on", -1)
            .limit(count)
            .to_list(count)
        )

    async def add_document(self, document):
        img_url = document.get("img_url")
        for link in document.get("links", []):
            parsed = urlparse(link["link"])
            link_path = parsed.path + ("?" + parsed.query if parsed.query else "")
            exists = await self.links_coll.find_one({"link": link_path})
            if not exists:
                new_doc = {
                    "img_url": img_url,
                    "name": link["name"],
                    "link": link_path,
                    "added_on": datetime.utcnow(),
                }
                await self.links_coll.insert_one(new_doc)
                logging.info(f"Inserted new document: {new_doc}")
                await send_new_link_notification([link])

db = Database(DATABASE_URL, "MadxBotz_Scrapper")
