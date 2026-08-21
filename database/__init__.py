import os
import re
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

import cloudscraper
import requests
from pyrogram import Client, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient

from configs import *

os.makedirs("downloads", exist_ok=True)
logging.basicConfig(level=logging.INFO)

scraper = cloudscraper.create_scraper(delay=10, browser="chrome")

User = Client("User", session_string=USER_SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# --- Async Utilities --- #

async def fetch(url):
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, lambda: scraper.get(url, headers=HTTP_HEADERS, timeout=20))
        response.raise_for_status()
        return response
    except Exception as e:
        logging.error(f"[fetch] Error fetching {url}: {e}")
        return None

async def download_file(url, local_filename):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await fetch(url)
            if response and response.content:
                with open(local_filename, "wb") as f:
                    f.write(response.content)

                if os.path.exists(local_filename) and os.path.getsize(local_filename) > 0:
                    logging.info(f"✅ Downloaded {local_filename} successfully.")
                    return True
                else:
                    if os.path.exists(local_filename):
                        os.remove(local_filename)
        except Exception as e:
            logging.error(f"[download_file] Error on attempt {attempt+1}: {e}")
        await asyncio.sleep(2)
    return False

async def send_new_link_notification(links):
    if not links:
        return

    # Check if User client is active before starting
    is_client_started = getattr(User, "is_connected", False)
    if not is_client_started:
        try:
            await User.start()
        except Exception:
            pass

    for link in links:
        local_filename = f"downloads/@AddaFileZ {link['name'].replace('/', '_')}.torrent"

        if await download_file(link["link"], local_filename):
            try:
                caption = f"<b>@AddaFileZ {link['name']}\n\n<blockquote>〽️ Powered by @AddaFileZ</blockquote></b>"
                
                buttons = []
                for dl in link.get("direct_links", []):
                    buttons.append([InlineKeyboardButton(f"⚡ Direct Link ({dl['name']})", url=dl["link"])])
                reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

                # 1. Send Document to Group
                sent_msg = await User.send_document(
                    chat_id=GROUP_ID,
                    document=local_filename,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )

                # 2. Trigger QBitLeech Command
                if sent_msg:
                    await User.send_message(
                        chat_id=GROUP_ID,
                        text="/qbleech1",
                        reply_to_message_id=sent_msg.id,
                    )

                # 3. Forward to RSS Channel
                if 'RSS_CHAT' in globals() and RSS_CHAT:
                    await User.send_document(
                        chat_id=RSS_CHAT,
                        document=local_filename,
                        caption=caption,
                        reply_markup=reply_markup,
                        parse_mode=enums.ParseMode.HTML
                    )
            except Exception as e:
                logging.error(f"[send_document] Telegram send error: {e}")
            finally:
                if os.path.exists(local_filename):
                    os.remove(local_filename)
        else:
            logging.warning(f"⚠️ Failed to download: {link['link']}")

# --- Database Class --- #

class Database:
    def __init__(self, url, db_name):
        self.db = AsyncIOMotorClient(url)[db_name]
        self.users_coll = self.db.users
        self.links_coll = self.db.attachments
        self.topics_coll = self.db.topics

    async def add_user(self, user_id):
        if not await self.is_present(user_id):
            await self.users_coll.insert_one({"id": user_id})

    async def is_present(self, user_id):
        return bool(await self.users_coll.find_one({"id": int(user_id)}))

    async def total_users(self):
        return await self.users_coll.count_documents({})

    async def count_all_links(self):
        return await self.links_coll.count_documents({})

    async def is_movie_present(self, page_url):
        return bool(await self.topics_coll.find_one({"url": page_url}))

    async def search_movie(self, movie_name):
        regex_query = {
            "name": {
                "$regex": re.escape(movie_name).replace(r"\ ", r".*"),
                "$options": "i",
            }
        }
        return await self.links_coll.find(regex_query).to_list(length=None)

    async def get_last_documents(self, count):
        return await self.links_coll.find().sort("added_on", -1).limit(count).to_list(count)

    async def add_document(self, document):
        page_url = document.get("page_url")
        img_url = document.get("img_url")
        links = document.get("links", [])

        # Mark whole topic as processed to prevent loop spamming
        if page_url:
            await self.topics_coll.update_one(
                {"url": page_url},
                {"$set": {"url": page_url, "added_on": datetime.utcnow()}},
                upsert=True
            )

        new_links_to_notify = []
        for link in links:
            parsed = urlparse(link["link"])
            link_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

            # Check if this exact attachment file is already saved
            if not await self.links_coll.find_one({"link": link_path}):
                new_doc = {
                    "img_url": img_url,
                    "name": link["name"],
                    "link": link_path,
                    "direct_links": link.get("direct_links", []),
                    "added_on": datetime.utcnow(),
                }
                await self.links_coll.insert_one(new_doc)
                logging.info(f"[DB] New document inserted: {new_doc['name']}")
                new_links_to_notify.append(link)

        # Send Telegram notification ONLY for newly inserted torrents
        if new_links_to_notify:
            await send_new_link_notification(new_links_to_notify)

# Global DB instance
db = Database(DATABASE_URL, "MadxBotz_Scrapper")
