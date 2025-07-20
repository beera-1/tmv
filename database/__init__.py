from motor.motor_asyncio import AsyncIOMotorClient
from configs import *
import os
from datetime import datetime
import logging
import re
from pyrogram import Client
import requests
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

executor = ThreadPoolExecutor()
os.makedirs("downloads", exist_ok=True)

User = Client(
    "User", session_string=USER_SESSION_STRING, api_hash=API_HASH, api_id=API_ID
)


async def fetch(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    }

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(executor, requests.get, url, headers)
        response.raise_for_status()
        return response, int(response.headers.get("Content-Length", 0))
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading {url}: {str(e)}")
        return None, 0


async def is_valid_link(url):
    response, _ = await fetch(url)
    return response is not None and response.status_code == 200


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
                    logging.error(
                        f"Downloaded file size does not match expected size for {url}. Attempt {attempt + 1}/{max_retries}."
                    )
                    os.remove(local_filename)
            else:
                logging.error(
                    f"Failed to fetch {url}. Attempt {attempt + 1}/{max_retries}."
                )

        except Exception as e:
            logging.error(
                f"Failed to download file from {url}: {e}. Attempt {attempt + 1}/{max_retries}."
            )

        await asyncio.sleep(1)

    logging.error(f"Failed to download file from {url} after {max_retries} attempts.")
    return False


async def send_new_link_notification(links):
    async with User:
        if not links:
            await User.send_message(chat_id=GROUP_ID, text="Empty Array")
            return

        for link in links:
            local_filename = f"downloads/@ADDAFILES {link['name']}.torrent"

            if await is_valid_link(link["link"]):
                if await download_file(link["link"], local_filename):
                    try:
                        sent_msg = await User.send_document(
                            chat_id=GROUP_ID,
                            document=local_filename,
                            thumb="database/thumb.jpg",
                            caption=f"""
<b>@ADDAFILES {link['name']}

<blockquote>〽️ Powered by @ADDAFILES</blockquote></b>""",
                        )

                        await User.send_message(
                            chat_id=GROUP_ID,
                            text="/qbleech",
                            reply_to_message_id=sent_msg.id,
                        )

                        sent_msg = await User.send_document(
                            chat_id=RSS_CHAT,
                            document=local_filename,
                            thumb="database/thumb.jpg",
                            caption=f"""
<b>@ADDAFILES {link['name']}

<blockquote>〽️ Powered by @ADDAFILES</blockquote></b>""",
                        )
                    except Exception as e:
                        logging.error(
                            f"Failed to send document for link {link['link']}: {e}"
                        )
                    finally:
                        if os.path.exists(local_filename):
                            os.remove(local_filename)
                else:
                    logging.warning(f"Failed to download file for link: {link['link']}")
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
        results = await self.links_coll.find(search_query).to_list(None)
        return results

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
            parsed_link = urlparse(link["link"])
            link_path = parsed_link.path + (
                "?" + parsed_link.query if parsed_link.query else ""
            )

            existing_link = await self.links_coll.find_one({"link": link_path})

            if not existing_link:
                new_document = {
                    "img_url": img_url,
                    "name": link["name"],
                    "link": link_path,
                    "added_on": datetime.utcnow(),
                }
                await self.links_coll.insert_one(new_document)
                print(f"New Document Inserted: {new_document}")
                await send_new_link_notification([link])


db = Database(DATABASE_URL, "MadxBotz_Scrapper")
