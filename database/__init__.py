import os
import re
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse, unquote

import cloudscraper
from pyrogram import Client
from motor.motor_asyncio import AsyncIOMotorClient

from configs import *


# ============================================================
# PREPARE ENVIRONMENT
# ============================================================

os.makedirs("downloads", exist_ok=True)
logging.basicConfig(level=logging.INFO)

# Robust path to thumb.jpg located in the same database directory
THUMB_PATH = os.path.join(os.path.dirname(__file__), "thumb.jpg")


# ============================================================
# CLOUDFLARE SCRAPER
# ============================================================

scraper = cloudscraper.create_scraper(
    delay=10,
    browser="chrome"
)


# ============================================================
# USER CLIENT
# ============================================================

User = Client(
    "User",
    session_string=USER_SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)


# ============================================================
# DOWNLOAD FILE
# ============================================================

async def download_file(url, local_filename):
    max_retries = 3
    loop = asyncio.get_event_loop()

    for attempt in range(max_retries):
        try:
            response = await loop.run_in_executor(
                None,
                lambda: scraper.get(url, stream=True, timeout=30)
            )

            if response and response.status_code == 200:
                expected_size = int(response.headers.get("Content-Length", 0))

                with open(local_filename, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                actual_size = os.path.getsize(local_filename)

                if expected_size == 0 or actual_size == expected_size:
                    logging.info(f"✅ Downloaded {local_filename} successfully ({actual_size} bytes).")
                    return True

                logging.warning(
                    f"❌ Size mismatch. Expected: {expected_size}, Downloaded: {actual_size}. Retrying..."
                )
                if os.path.exists(local_filename):
                    os.remove(local_filename)
            else:
                status = response.status_code if response else "No response"
                logging.warning(f"⚠️ Fetch failed (Status {status}): {url} (attempt {attempt + 1}/{max_retries})")

        except Exception as e:
            logging.error(f"[download_file] Error: {e}", exc_info=True)

        await asyncio.sleep(2)

    if os.path.exists(local_filename):
        os.remove(local_filename)

    logging.error(f"❌ Failed to download {url} after {max_retries} attempts.")
    return False


# ============================================================
# CLEAN TORRENT NAME
# ============================================================

def clean_filename(name):
    if not name:
        name = "Unknown"

    name = unquote(str(name).strip())

    # Remove unwanted site domain prefixes
    name = re.sub(
        r"^\s*(?:www\.)?[a-zA-Z0-9-]+\.(?:com|net|org|in|co|cc|me|tv|to|io|site|online|xyz)(?:\s*[-_:]\s*|\s+)",
        "",
        name,
        flags=re.IGNORECASE
    )

    # Sanitize invalid filesystem characters
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    # Ensure .torrent suffix
    if not name.lower().endswith(".torrent"):
        name += ".torrent"

    # Ensure @AddaFileZ prefix
    prefix = "@AddaFileZ"
    if not name.lower().startswith(prefix.lower()):
        name = f"{prefix} - {name}"

    return name


# ============================================================
# SEND NEW LINK NOTIFICATION
# ============================================================

async def send_new_link_notification(links):
    if not User.is_connected:
        await User.start()

    if not links:
        await User.send_message(chat_id=GROUP_ID, text="Empty Array")
        return

    for link in links:
        filename = clean_filename(link.get("name", "file"))
        local_filename = os.path.join("downloads", filename)

        logging.info(f"📁 Processing: {filename}")

        downloaded = await download_file(link["link"], local_filename)
        if not downloaded:
            continue

        try:
            clean_name = os.path.basename(local_filename)
            caption = (
                f"<b>{clean_name}\n\n"
                f"#Movies #1TMV\n\n"
                f"Powered By ✨ @AddaFileZ</b>"
            )

            thumb = THUMB_PATH if os.path.exists(THUMB_PATH) else None

            # 1. Send file to group
            sent_msg = await User.send_document(
                chat_id=GROUP_ID,
                document=local_filename,
                thumb=thumb,
                caption=caption
            )

            # 2. Trigger leech command in group
            if sent_msg:
                await User.send_message(
                    chat_id=GROUP_ID,
                    text="/qbleech1",
                    reply_to_message_id=sent_msg.id
                )

            # 3. Send file to RSS channel
            await User.send_document(
                chat_id=RSS_CHAT,
                document=local_filename,
                thumb=thumb,
                caption=caption
            )

            logging.info(f"✅ Sent successfully: {clean_name}")

        except Exception as e:
            logging.error(f"[send_document] Error: {e}", exc_info=True)

        finally:
            if os.path.exists(local_filename):
                try:
                    os.remove(local_filename)
                    logging.info(f"🗑️ Removed: {local_filename}")
                except Exception as e:
                    logging.warning(f"Could not remove {local_filename}: {e}")


# ============================================================
# DATABASE
# ============================================================

class Database:

    def __init__(self, url, db_name):
        self.db = AsyncIOMotorClient(url)[db_name]
        self.users_coll = self.db.users
        self.links_coll = self.db.attachments

    async def add_user(self, user_id):
        if not await self.is_present(user_id):
            await self.users_coll.insert_one({"id": int(user_id)})

    async def is_present(self, user_id):
        return bool(await self.users_coll.find_one({"id": int(user_id)}))

    async def total_users(self):
        return await self.users_coll.count_documents({})

    async def count_all_links(self):
        return await self.links_coll.count_documents({})

    async def search_movie(self, movie_name):
        regex_query = {
            "name": {
                "$regex": re.escape(movie_name).replace(r"\ ", r".*"),
                "$options": "i",
            }
        }
        return await self.links_coll.find(regex_query).to_list(length=None)

    async def get_last_documents(self, count):
        return await self.links_coll.find({}).sort("added_on", -1).limit(count).to_list(count)

    async def add_document(self, document):
        img_url = document.get("img_url")

        for link in document.get("links", []):
            parsed = urlparse(link["link"])
            link_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

            # Prevent duplicate processing
            if await self.links_coll.find_one({"link": link_path}):
                continue

            new_doc = {
                "img_url": img_url,
                "name": link["name"],
                "link": link_path,
                "added_on": datetime.utcnow(),
            }

            await self.links_coll.insert_one(new_doc)
            logging.info(f"[DB] New document inserted: {new_doc['name']}")

            await send_new_link_notification([link])


# ============================================================
# GLOBAL DATABASE INSTANCE
# ============================================================

db = Database(DATABASE_URL, "MadxBotz_Scrapper")
