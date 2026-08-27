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

logging.basicConfig(
    level=logging.INFO
)


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
# FETCH
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


# ============================================================
# CHECK LINK
# ============================================================

async def is_valid_link(url):

    response, _ = await fetch(url)

    return (
        response.status_code == 200
        if response
        else False
    )


# ============================================================
# DOWNLOAD FILE
# ============================================================

async def download_file(
    url,
    local_filename
):

    max_retries = 5

    for attempt in range(max_retries):

        try:

            response, expected_size = await fetch(
                url
            )

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

                # Some servers don't send Content-Length.
                if (
                    expected_size == 0
                    or actual_size == expected_size
                ):

                    logging.info(
                        f"✅ Downloaded "
                        f"{local_filename} successfully."
                    )

                    return True

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
# CLEAN TORRENT NAME
# ============================================================

def clean_filename(name):

    if not name:
        name = "Unknown"

    # Decode URL encoded characters.
    name = unquote(
        str(name).strip()
    )

    # --------------------------------------------------------
    # DO NOT REMOVE:
    #
    # @AddaFileZ
    # ESub
    # ESub.mkv
    # .mkv
    # .torrent
    # --------------------------------------------------------

    # Remove ONLY common website prefixes.
    name = re.sub(
        r"^\s*(?:www\.)?"
        r"[a-zA-Z0-9-]+\."
        r"(?:com|net|org|in|co|cc|me|tv|to|io|site|online|xyz)"
        r"(?:\s*[-_:]\s*|\s+)",
        "",
        name,
        flags=re.IGNORECASE
    )

    # Remove invalid filesystem characters.
    name = re.sub(
        r'[\\/*?:"<>|]',
        "_",
        name
    )

    name = name.strip()

    # --------------------------------------------------------
    # KEEP .torrent
    # --------------------------------------------------------

    if not name.lower().endswith(
        ".torrent"
    ):

        name += ".torrent"

    # --------------------------------------------------------
    # KEEP @AddaFileZ PREFIX
    # --------------------------------------------------------

    prefix = "@AddaFileZ"

    if not name.lower().startswith(
        prefix.lower()
    ):

        name = (
            f"{prefix} - {name}"
        )

    return name


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

            # =================================================
            # CREATE FINAL FILENAME
            # =================================================

            filename = clean_filename(
                link["name"]
            )

            local_filename = os.path.join(
                "downloads",
                filename
            )

            logging.info(
                f"📁 Final filename: {filename}"
            )


            # =================================================
            # CHECK LINK
            # =================================================

            if not await is_valid_link(
                link["link"]
            ):

                logging.warning(
                    f"⚠️ Invalid link skipped: "
                    f"{link['link']}"
                )

                continue


            # =================================================
            # DOWNLOAD
            # =================================================

            downloaded = await download_file(
                link["link"],
                local_filename
            )

            if not downloaded:

                logging.warning(
                    f"⚠️ Failed to download: "
                    f"{link['link']}"
                )

                continue


            try:

                # =================================================
                # USE EXACT FINAL FILENAME
                #
                # This keeps:
                # @AddaFileZ
                # ESub.mkv
                # .torrent
                # =================================================

                clean_name = os.path.basename(
                    local_filename
                )


                # =================================================
                # FINAL CAPTION
                # =================================================

                caption = (
                    f"<b>{clean_name}"
                    f"\n\n"
                    f"#Movies #1TMV"
                    f"\n\n"
                    f"Powered By ✨ @AddaFileZ"
                    f"</b>"
                )


                # =================================================
                # SEND TO GROUP
                # =================================================

                sent_msg = await User.send_document(
                    chat_id=GROUP_ID,
                    document=local_filename,
                    thumb="database/thumb.jpg",
                    caption=caption
                )


                # =================================================
                # TRIGGER COMMAND
                # =================================================

                await User.send_message(
                    chat_id=GROUP_ID,
                    text="/qbleech1",
                    reply_to_message_id=sent_msg.id
                )


                # =================================================
                # SEND TO RSS CHAT
                # =================================================

                await User.send_document(
                    chat_id=RSS_CHAT,
                    document=local_filename,
                    thumb="database/thumb.jpg",
                    caption=caption
                )


                logging.info(
                    f"✅ Sent successfully: "
                    f"{clean_name}"
                )


            except Exception as e:

                logging.error(
                    f"[send_document] Error: {e}",
                    exc_info=True
                )


            finally:

                # =================================================
                # DELETE LOCAL FILE
                # =================================================

                if os.path.exists(
                    local_filename
                ):

                    try:

                        os.remove(
                            local_filename
                        )

                        logging.info(
                            f"🗑️ Removed: "
                            f"{local_filename}"
                        )

                    except Exception as e:

                        logging.warning(
                            f"Could not remove "
                            f"{local_filename}: {e}"
                        )


# ============================================================
# DATABASE
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
    # COUNT LINKS
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


            # =================================================
            # DUPLICATE CHECK
            # =================================================

            if await self.links_coll.find_one(
                {
                    "link": link_path
                }
            ):

                continue


            # =================================================
            # STORE DOCUMENT
            # =================================================

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
            # SEND TELEGRAM
            # =================================================

            await send_new_link_notification(
                [link]
            )


# ============================================================
# GLOBAL DATABASE INSTANCE
# ============================================================

db = Database(
    DATABASE_URL,
    "MadxBotz_Scrapper"
)
