import os
import re
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

import cloudscraper
import requests
from pyrogram import Client, enums
from motor.motor_asyncio import AsyncIOMotorClient

from configs import *


# ============================================================
# PREPARE ENVIRONMENT
# ============================================================

os.makedirs("downloads", exist_ok=True)

logging.basicConfig(
    level=logging.INFO
)


# ============================================================
# THREADED SCRAPER FOR CLOUDFLARE-BYPASSED REQUESTS
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
# ASYNC UTILITIES
# ============================================================

async def fetch(url):
    loop = asyncio.get_event_loop()

    try:
        response = await loop.run_in_executor(
            None,
            lambda: scraper.get(
                url,
                timeout=15
            )
        )

        response.raise_for_status()

        size = int(
            response.headers.get(
                "Content-Length",
                0
            )
        )

        return response, size

    except Exception as e:
        logging.error(
            f"[fetch] Error fetching {url}: {e}",
            exc_info=True
        )

        return None, 0


async def is_valid_link(url):
    response, _ = await fetch(url)

    return (
        response.status_code == 200
        if response
        else False
    )


async def download_file(url, local_filename):
    max_retries = 5

    for attempt in range(max_retries):

        try:
            response, expected_size = await fetch(url)

            if response:

                with open(
                    local_filename,
                    "wb"
                ) as f:

                    for chunk in response.iter_content(
                        chunk_size=8192
                    ):
                        f.write(chunk)

                actual_size = os.path.getsize(
                    local_filename
                )

                # If server does not provide Content-Length,
                # accept the downloaded file.
                if (
                    expected_size == 0
                    or actual_size == expected_size
                ):

                    logging.info(
                        f"✅ Downloaded "
                        f"{local_filename} successfully."
                    )

                    return True

                else:

                    logging.warning(
                        f"❌ Size mismatch. "
                        f"Expected: {expected_size}, "
                        f"Downloaded: {actual_size}. "
                        f"Retrying..."
                    )

                    if os.path.exists(
                        local_filename
                    ):
                        os.remove(
                            local_filename
                        )

            else:

                logging.warning(
                    f"⚠️ Fetch failed: {url} "
                    f"(attempt {attempt + 1}/"
                    f"{max_retries})"
                )

        except Exception as e:

            logging.error(
                f"[download_file] Error: {e}",
                exc_info=True
            )

        await asyncio.sleep(2)

    logging.error(
        f"❌ Failed to download {url} "
        f"after {max_retries} attempts."
    )

    return False


# ============================================================
# CLEAN CAPTION NAME
# ============================================================

def clean_caption_name(name):
    """
    Removes only torrent/file extensions from the caption.

    IMPORTANT:
    @AddaFileZ prefix is NOT removed here.
    """

    if not name:
        return ""

    name = str(name).strip()

    # Remove .mkv.torrent
    name = re.sub(
        r"\.mkv\.torrent$",
        "",
        name,
        flags=re.IGNORECASE
    )

    # Remove .torrent
    name = re.sub(
        r"\.torrent$",
        "",
        name,
        flags=re.IGNORECASE
    )

    return name.strip()


# ============================================================
# SEND NEW LINK NOTIFICATION
# ============================================================

async def send_new_link_notification(links):

    async with User:

        if not links:

            await User.send_message(
                chat_id=GROUP_ID,
                text="Empty Array"
            )

            return

        for link in links:

            # ====================================================
            # DO NOT CHANGE THIS
            # YOUR OLD @AddaFileZ PREFIX IS KEPT
            # ====================================================

            local_filename = (
                f"downloads/@AddaFileZ "
                f"{link['name']}.torrent"
            )

            # ====================================================
            # CHECK LINK
            # ====================================================

            if await is_valid_link(
                link["link"]
            ):

                # =================================================
                # DOWNLOAD TORRENT
                # =================================================

                if await download_file(
                    link["link"],
                    local_filename
                ):

                    try:

                        # ==========================================
                        # CLEAN ONLY THE CAPTION NAME
                        # ==========================================

                        movie_name = clean_caption_name(
                            link["name"]
                        )

                        # ==========================================
                        # EXACT CAPTION FORMAT
                        # ==========================================

                        caption = (
                            f"<b>"
                            f"@AddaFileZ - {movie_name}"
                            f"\n\n"
                            f"#Movies #1TMV"
                            f"\n\n"
                            f"Powered By ✨ @AddaFileZ"
                            f"</b>"
                        )

                        # ==========================================
                        # SEND TO GROUP
                        # ==========================================

                        sent_msg = await User.send_document(
                            chat_id=GROUP_ID,
                            document=local_filename,
                            thumb="database/thumb.jpg",
                            caption=caption,
                            parse_mode=enums.ParseMode.HTML,
                        )

                        # ==========================================
                        # TRIGGER COMMAND
                        # ==========================================

                        await User.send_message(
                            chat_id=GROUP_ID,
                            text="/qbleech1",
                            reply_to_message_id=sent_msg.id,
                        )

                        # ==========================================
                        # SEND TO RSS CHAT
                        # ==========================================

                        await User.send_document(
                            chat_id=RSS_CHAT,
                            document=local_filename,
                            thumb="database/thumb.jpg",
                            caption=caption,
                            parse_mode=enums.ParseMode.HTML,
                        )

                    except Exception as e:

                        logging.error(
                            f"[send_document] Error: {e}",
                            exc_info=True
                        )

                    finally:

                        # ==========================================
                        # REMOVE DOWNLOADED FILE
                        # ==========================================

                        if os.path.exists(
                            local_filename
                        ):

                            try:
                                os.remove(
                                    local_filename
                                )

                            except Exception as e:

                                logging.warning(
                                    f"Could not remove "
                                    f"{local_filename}: {e}"
                                )

                else:

                    logging.warning(
                        f"⚠️ Failed to download: "
                        f"{link['link']}"
                    )

            else:

                logging.warning(
                    f"⚠️ Invalid link skipped: "
                    f"{link['link']}"
                )


# ============================================================
# DATABASE CLASS
# ============================================================

class Database:

    def __init__(
        self,
        url,
        db_name
    ):

        self.db = AsyncIOMotorClient(
            url
        )[db_name]

        self.users_coll = self.db.users

        self.links_coll = self.db.attachments


    # ========================================================
    # ADD USER
    # ========================================================

    async def add_user(
        self,
        user_id
    ):

        if not await self.is_present(
            user_id
        ):

            await self.users_coll.insert_one(
                {
                    "id": user_id
                }
            )


    # ========================================================
    # CHECK USER
    # ========================================================

    async def is_present(
        self,
        user_id
    ):

        return bool(
            await self.users_coll.find_one(
                {
                    "id": int(user_id)
                }
            )
        )


    # ========================================================
    # TOTAL USERS
    # ========================================================

    async def total_users(self):

        return await self.users_coll.count_documents(
            {}
        )


    # ========================================================
    # COUNT ALL LINKS
    # ========================================================

    async def count_all_links(self):

        return await self.links_coll.count_documents(
            {}
        )


    # ========================================================
    # SEARCH MOVIE
    # ========================================================

    async def search_movie(
        self,
        movie_name
    ):

        regex_query = {
            "name": {
                "$regex": re.escape(
                    movie_name
                ).replace(
                    r"\ ",
                    r".*"
                ),
                "$options": "i",
            }
        }

        return await self.links_coll.find(
            regex_query
        ).to_list(
            length=None
        )


    # ========================================================
    # GET LAST DOCUMENTS
    # ========================================================

    async def get_last_documents(
        self,
        count
    ):

        return await self.links_coll.find(
            {}
        ).sort(
            "added_on",
            -1
        ).limit(
            count
        ).to_list(
            count
        )


    # ========================================================
    # ADD DOCUMENT
    # ========================================================

    async def add_document(
        self,
        document
    ):

        img_url = document.get(
            "img_url"
        )

        for link in document.get(
            "links",
            []
        ):

            parsed = urlparse(
                link["link"]
            )

            link_path = (
                parsed.path
                + (
                    f"?{parsed.query}"
                    if parsed.query
                    else ""
                )
            )

            # ====================================================
            # CHECK DUPLICATE
            # ====================================================

            if not await self.links_coll.find_one(
                {
                    "link": link_path
                }
            ):

                new_doc = {
                    "img_url": img_url,
                    "name": link["name"],
                    "link": link_path,
                    "added_on": datetime.utcnow(),
                }

                await self.links_coll.insert_one(
                    new_doc
                )

                logging.info(
                    f"[DB] New document inserted: "
                    f"{new_doc['name']}"
                )

                # =================================================
                # SEND TELEGRAM NOTIFICATION
                # =================================================

                await send_new_link_notification(
                    [link]
                )


# ============================================================
# GLOBAL DB INSTANCE
# ============================================================

db = Database(
    DATABASE_URL,
    "MadxBotz_Scrapper"
)
