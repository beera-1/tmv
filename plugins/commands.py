import asyncio
import base64
import logging
import re
import urllib.parse
import aiohttp
from bs4 import BeautifulSoup

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    CallbackQuery,
    Message,
)

from configs import *
from database import db
from utilities import fetch

user_pagination = {}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ==========================================
# Cyberloom / MessyCloud Bypass Engine
# ==========================================
async def resolve_cyberloom(start_url: str) -> dict:
    """
    Follows multi-hop redirects and resolves direct download links
    from Cyberloom / Inkvoyage / MessyCloud landing pages.
    """
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

            raw_title = soup3.find("h1").text.strip() if soup3.find("h1") else "Direct File Download"
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
                    direct_links.append({"label": label, "url": final_download_url})

            return {
                "success": True,
                "url": start_url,
                "title": cleaned_title,
                "size": file_size,
                "links": direct_links,
            }

        except Exception as err:
            return {"success": False, "url": start_url, "error": str(err)}


# ==========================================
# Chunking & Formatting Output Helper
# ==========================================
async def send_split_search_results(client: Client, message: Message, movie_name: str, movie_docs: list):
    img_url = movie_docs[0].get("img_url", None)
    links = [(doc.get("name"), doc.get("link")) for doc in movie_docs]

    header = f"<b>Query: {movie_name}</b>\n\n"
    if img_url:
        header += f"<b>Image URL:\n<code>{img_url}</code></b>\n\n"

    if not links:
        final_msg = header + "<b>No links available.</b>\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"
        if img_url:
            await message.reply_photo(photo=img_url, caption=final_msg[:1024] if len(final_msg) <= 1024 else None)
            if len(final_msg) > 1024:
                await message.reply_text(final_msg)
        else:
            await message.reply_text(final_msg)
        return

    if img_url:
        try:
            await message.reply_photo(photo=img_url)
        except Exception:
            pass

    footer = "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"
    chunks = []
    current_chunk = header + "<b>Available Torrent Links:</b>"

    for name, link in links:
        link_entry = f"<b>\n\n🗳 {name}\n\n🧲 • <code>{BASE_URL + link}</code></b>"
        if len(current_chunk + link_entry + footer) > 3800:
            chunks.append(current_chunk)
            current_chunk = link_entry
        else:
            current_chunk += link_entry

    current_chunk += footer
    chunks.append(current_chunk)

    for part in chunks:
        await message.reply_text(part)


# ==========================================
# Telegram Bot Handlers
# ==========================================

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(c: Client, m: Message):
    try:
        user_id = m.from_user.id
        if not await db.is_present(user_id):
            await db.add_user(user_id)
            await c.send_message(
                chat_id=GROUP_ID,
                text=(
                    f"<b>New User Started The Bot\n\n"
                    f"User: <a href='tg://openmessage?user_id={user_id}'>View User</a>\n\n"
                    f"User ID: {user_id}</b>"
                ),
                parse_mode=enums.ParseMode.HTML,
            )

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Hᴇʟᴩ Mᴇɴᴜ", callback_data="help")],
                [
                    InlineKeyboardButton("Cʜᴀɴɴᴇʟ", url="https://t.me/MOVIES_ADDDDA"),
                    InlineKeyboardButton("Sᴜᴩᴩᴏʀᴛ", url="https://t.me/MOVIES_ADDDDA"),
                ],
                [InlineKeyboardButton("Cʟᴏsᴇ ❌", callback_data="delete")],
            ]
        )
        await m.reply_text(START_TXT.format(m.from_user.mention), reply_markup=keyboard)
    except Exception as e:
        await m.reply_text(f"Error: {e}")


@Client.on_message(filters.private & filters.command("cb"))
async def cyberloom_bypass_handler(client: Client, message: Message):
    """
    Command /cb <url1> <url2> ...
    Supports single or multiple space/newline-separated Cyberloom / MessyCloud links.
    """
    raw_text = message.text.strip()
    command_args = raw_text.split(None, 1)

    if len(command_args) < 2:
        await message.reply_text(
            "<b>Please provide one or more links.</b>\n\n"
            "<b>Usage:</b>\n"
            "<code>/cb https://www.cyberloom.best/l/1 https://www.cyberloom.best/l/2</code>"
        )
        return

    # Extract all valid URLs from input
    urls = re.findall(r"https?://[^\s]+", command_args[1])
    if not urls:
        await message.reply_text("❌ <b>No valid URLs found in your message.</b>")
        return

    status_msg = await message.reply_text(f"⚡ <b>Bypassing {len(urls)} link(s), please wait...</b>")

    # Run bypass concurrently for all extracted links
    tasks = [resolve_cyberloom(u) for u in urls]
    results = await asyncio.gather(*tasks)

    try:
        await status_msg.delete()
    except Exception:
        pass

    for res in results:
        if not res.get("success") or not res.get("links"):
            err_text = (
                f"❌ <b>Bypass Failed</b>\n"
                f"🔗 <b>URL:</b> <code>{res.get('url')}</code>\n"
                f"⚠️ <b>Reason:</b> <code>{res.get('error', 'No endpoints detected')}</code>"
            )
            await message.reply_text(err_text)
            continue

        title = res["title"]
        size = res["size"]
        links = res["links"]

        caption = (
            f"🎬 <b>File:</b> <code>{title}</code>\n"
            f"📦 <b>Size:</b> <code>{size}</code>\n\n"
            f"⚡ <b>Direct Links:</b>"
        )

        buttons = []
        for item in links:
            buttons.append([InlineKeyboardButton(f"🚀 {item['label']}", url=item["url"])])

        buttons.append([InlineKeyboardButton("❌ Close", callback_data="delete")])

        await message.reply_text(
            caption,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True
        )


@Client.on_message(filters.private & filters.command("total_scraps"))
async def link_count(c: Client, m: Message):
    try:
        total_link_count = await db.count_all_links()
        msg = f"<b>📍Total Movies Scrapped : <code>{total_link_count}</code> \n\n<blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"
        await m.reply_text(msg)
    except Exception as e:
        await m.reply_text(f"🌶️ Error retrieving counts: {e}")


@Client.on_message(filters.private & filters.command(["scrap"]))
async def page_scrap(client: Client, message: Message):
    page_url_msg = message.text.strip()
    if not page_url_msg.startswith("/scrap "):
        await message.reply_text("<b>Please use the format: <code>/scrap link</code></b>")
        return

    page_url = page_url_msg[7:].strip()
    if not page_url:
        await message.reply_text("<b>Please provide a page URL after the command.</b>")
        return

    if BASE_URL + WEEK_RELEASES_PATH not in page_url:
        await message.reply_text("<b>Command is only used to scrap from 1Tamilmv Site</b>")
        return

    try:
        html = await fetch(page_url)
        if not html:
            await message.reply_text(f"<b>Unable to retrieve content for '{page_url}'.</b>")
            return

        soup = BeautifulSoup(html, "html.parser")
        content_div = soup.find("div", class_="ipsType_richText")
        img_url = None
        if content_div:
            img_tag = content_div.find("img")
            if img_tag and img_tag.get("src"):
                img_url = img_tag["src"]

        links = []
        for link in soup.find_all("a", href=True):
            if "attachment.php" in link["href"]:
                links.append({"name": link.get_text(strip=True), "link": link["href"]})

        caption = f"<b>Query : <code>{page_url}</code></b>\n\n"
        if img_url:
            caption += f"Image URL:\n<code>{img_url}</code>\n\n"
        else:
            caption += f"<b>Movie found, but no image available.</b>\n\n"

        captions = []
        if links:
            caption += "<b>Available Torrent Links:</b>"
            for i in range(0, len(links), 10):
                chunk = links[i : i + 10]
                chunk_text = ""
                for link in chunk:
                    chunk_text += f"<b>\n\n🗳 {link['name']}\n\n🧲 • <code>{link['link']}</code></b>"

                if len(caption + chunk_text) > 1024:
                    captions.append(caption)
                    caption = chunk_text
                else:
                    caption += chunk_text
            captions.append(caption)
        else:
            caption += "<b>\n\nNo links available.</b>"
            captions.append(caption)

        captions[-1] += "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

        if img_url:
            await message.reply_photo(photo=img_url)

        for part in captions:
            await message.reply_text(part)

    except Exception as e:
        await message.reply_text(f"Error occurred while processing: {e}")


@Client.on_message(filters.private & filters.command("get"))
async def movie_result_2(client: Client, message: Message):
    command_text = message.text.strip()
    if not command_text.startswith("/get "):
        await message.reply_text("<b>Usage: <code>/get Movie Name (Year)</code></b>")
        return

    movie_name = command_text[5:].strip()
    if not movie_name:
        await message.reply_text("<b>Please provide a movie name and year.</b>")
        return

    try:
        movie_docs = await db.search_movie(movie_name)
        if movie_docs:
            await send_split_search_results(client, message, movie_name, movie_docs)
        else:
            await message.reply_text(f"<b>Could not find any movie matching '{movie_name}'.</b>")
    except Exception as e:
        await message.reply_text(f"Error occurred while searching: {e}")


@Client.on_message(filters.private & filters.command("list"))
async def list_documents(client: Client, message: Message):
    user_id = message.from_user.id
    user_pagination[user_id] = {"current_index": 0}

    try:
        documents = await db.get_last_documents(40)
        paginated_docs = []

        for document in documents:
            caption_parts = split_caption(document)
            paginated_docs.extend(caption_parts)

        if paginated_docs:
            user_pagination[user_id]["documents"] = paginated_docs
            await send_initial_document(client, message, user_id, 0)
        else:
            await message.reply_text("<b>No documents found.</b>")
    except Exception as e:
        await message.reply_text(f"Error retrieving documents: {e}")


@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(["start", "help", "list", "get", "scrap", "total_scraps", "cb"])
)
async def movie_result_1(client: Client, message: Message):
    movie_name = message.text.strip()
    if BASE_URL + WEEK_RELEASES_PATH in movie_name:
        await message.reply_text("<b>Use /scrap command to Scrap the Links from Page</b>")
        return

    try:
        movie_docs = await db.search_movie(movie_name)
        if movie_docs:
            await send_split_search_results(client, message, movie_name, movie_docs)
        else:
            await message.reply_text(f"<b>Could not find any movie matching '{movie_name}'.</b>")
    except Exception as e:
        await message.reply_text(f"Error occurred while searching: {e}")


# ==========================================
# Callback Query Handlers & Pagination
# ==========================================

@Client.on_callback_query()
async def callback_handler(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data == "delete":
        await query.message.delete()
        await query.answer()
        return

    if data == "help":
        help_text = (
            "<b>Bot Commands:</b>\n\n"
            "• <code>/start</code> - Start the bot\n"
            "• <code>/cb &lt;link1&gt; &lt;link2&gt;</code> - Bypass multiple Cyberloom links\n"
            "• <code>/get &lt;movie name&gt;</code> - Search movie in DB\n"
            "• <code>/scrap &lt;page link&gt;</code> - Extract links from URL\n"
            "• <code>/list</code> - View recent movies\n"
            "• <code>/total_scraps</code> - Check total database entries"
        )
        await query.answer()
        await query.message.reply_text(help_text)
        return

    if data.startswith("next_") or data.startswith("prev_"):
        parts = data.split("_")
        target_user_id = int(parts[1])
        target_index = int(parts[2])

        if user_id != target_user_id:
            await query.answer("This menu belongs to another user!", show_alert=True)
            return

        if target_user_id not in user_pagination or "documents" not in user_pagination[target_user_id]:
            await query.answer("Session expired. Please run /list again.", show_alert=True)
            return

        await query.answer()
        await show_document(client, query.message, target_user_id, target_index)


async def send_initial_document(client: Client, message: Message, user_id: int, index: int):
    documents = user_pagination[user_id]["documents"]
    document = documents[index]
    img_url = document.get("img_url", None)

    buttons = [InlineKeyboardButton("❌", callback_data="delete")]
    if len(documents) > 1:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"next_{user_id}_{index + 1}"))

    reply_markup = InlineKeyboardMarkup([buttons])
    caption = document.get("caption", "No Caption") + "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

    if img_url:
        await message.reply_photo(photo=img_url, caption=caption, reply_markup=reply_markup)
    else:
        await message.reply_text(caption, reply_markup=reply_markup)


async def show_document(client: Client, message: Message, user_id: int, index: int):
    documents = user_pagination[user_id]["documents"]
    document = documents[index]
    img_url = document.get("img_url", None)

    buttons = []
    if index > 0:
        buttons.append(InlineKeyboardButton("⬅️", callback_data=f"prev_{user_id}_{index - 1}"))
    buttons.append(InlineKeyboardButton("❌", callback_data="delete"))
    if index < len(documents) - 1:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"next_{user_id}_{index + 1}"))

    reply_markup = InlineKeyboardMarkup([buttons])
    caption = document.get("caption", "No Caption") + "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

    try:
        if img_url:
            if message.photo:
                await message.edit_media(
                    media=InputMediaPhoto(media=img_url, caption=caption),
                    reply_markup=reply_markup,
                )
            else:
                await message.delete()
                await message.reply_photo(photo=img_url, caption=caption, reply_markup=reply_markup)
        else:
            if message.photo:
                await message.delete()
                await message.reply_text(caption, reply_markup=reply_markup)
            else:
                await message.edit_text(caption, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error updating paginated document: {e}")


def split_caption(document: dict):
    caption = f"<b>Title: {document.get('name', 'Unknown Movie')}</b>\n\n"
    img_url = document.get("img_url", None)
    if img_url:
        caption += f"<b>Image URL:\n<code>{img_url}</code></b>\n\n"
    else:
        caption += "<b>No image available.</b>\n\n"

    link_name = document.get("name", "No Name")
    link_url = document.get("link", "No Link")
    caption += f"<b>Available Torrent Link:\n\n🗳 {link_name}\n\n🧲 • <code>{BASE_URL + link_url}</code>\n</b>"

    max_caption_length = 1000
    caption_parts = []
    while len(caption) > max_caption_length:
        split_point = caption[:max_caption_length].rfind("\n")
        if split_point == -1:
            split_point = max_caption_length
        caption_parts.append(
            {
                "caption": caption[:split_point],
                "img_url": img_url,
                "text": document.get("name", "Unknown Movie"),
            }
        )
        caption = caption[split_point:]

    caption_parts.append(
        {
            "caption": caption,
            "img_url": img_url,
            "text": document.get("name", "Unknown Movie"),
        }
    )
    return caption_parts
