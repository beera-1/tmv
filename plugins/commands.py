from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from configs import *
from pyrogram import Client, filters, __version__, enums
from pyrogram.types import InputMediaPhoto
from utilities import fetch
from bs4 import BeautifulSoup
import logging


@Client.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    try:
        id = m.from_user.id

        if not await db.is_present(id):
            await db.add_user(id)

            await c.send_message(
                chat_id=GROUP_ID,
                text=f"<b>New User Started The Bot\n\nUser: <a href='tg://openmessage?user_id={id}'>View User</a>\n\nUser ID: {id}</b>",
                parse_mode=enums.ParseMode.HTML,
            )

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Hᴇʟᴩ Mᴇɴᴜ", callback_data="help")],
                [
                    InlineKeyboardButton("Cʜᴀɴɴᴇʟ", url="https://t.me/MOVIES_ADDDDA"),
                    InlineKeyboardButton(
                        "Sᴜᴩᴩᴏʀᴛ", url=f"https://t.me/MOVIES_ADDDDA"
                    ),
                ],
                [InlineKeyboardButton("Cʟᴏsᴇ ❌", callback_data="delete")],
            ]
        )

        await m.reply_text(START_TXT.format(m.from_user.mention), reply_markup=keyboard)
    except Exception as e:
        await m.reply_text(f"Error: {e}")


@Client.on_message(filters.private & filters.command("total_scraps"))
async def link_count(c, m):
    user_id = m.from_user.id
    try:
        total_link_count = await db.count_all_links()

        msg = f"<b>📍Total Movies Scrapped : <code>{total_link_count}</code> \n\n<blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"
        await m.reply_text(msg)
    except Exception as e:
        await m.reply_text(f"🌶️ Error retrieving counts: {e}")


@Client.on_message(
    filters.private
    & filters.text
    & filters.command(["scrap"])
    & ~filters.command(["list", "get"])
)
async def page_scrap(client, message):

    page_url_msg = message.text.strip()

    if not page_url_msg.startswith("/scrap "):
        await message.reply_text(
            "<b>Please use the command in the format: <code>/scrap link</code></b>"
        )
        return

    page_url = page_url_msg[7:].strip()

    if not page_url:
        await message.reply_text("<b>Please provide a page url after the command.</b>")
        return

    if not BASE_URL + WEEK_RELEASES_PATH in page_url:
        await message.reply_text(
            "<b>Command is only used to scrap from 1Tamilmv Site</b>"
        )
        return

    user_id = message.from_user.id

    try:
        html = await fetch(page_url)
        if not html:
            await message.reply_text(
                f"<b>Unable to retrieve the content for the provided link '{page_url}'.</b>"
            )
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
                link_text = link.get_text(strip=True)
                links.append({"name": link_text, "link": link["href"]})

        if img_url:
            caption = f"<b>Query : <code>{page_url}</code></b>\n\n"
            caption += f"Image URL:\n<code>{img_url}</code>\n\n"
        else:
            caption = (
                f"<b>Movie found, but no image available for '{page_url}'.</b>\n\n"
            )

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


@Client.on_message(filters.private & filters.text & ~filters.command(["list", "get"]))
async def movie_result_1(client, message):
    movie_name = message.text.strip()
    user_id = message.from_user.id

    if BASE_URL + WEEK_RELEASES_PATH in movie_name:
        await message.reply_text(
            "<b>Use /scrap command to Scrap the Links from Page</b>"
        )
        return

    try:
        movie_docs = await db.search_movie(movie_name)

        if movie_docs:
            img_url = movie_docs[0].get("img_url", None)
            links = [(doc.get("name"), doc.get("link")) for doc in movie_docs]

            if img_url:
                caption = f"<b>Query: {movie_name}</b>\n\n"
                caption += f"<b>Image URL:\n<code>{img_url}</code></b>\n\n"
            else:
                caption = f"<b>Movie found, but no image available for '{movie_name}'.</b>\n\n"

            if links:
                caption += "<b>Available Torrent Links:</b>"
                for name, link in links:
                    caption += (
                        f"<b>\n\n🗳 {name}\n\n🧲 • <code>{BASE_URL+link}</code></b>"
                    )
            else:
                caption += "<b>\n\nNo links available.</b>"

            caption += "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

            if len(caption) > 1000:
                if img_url:
                    await message.reply_photo(photo=img_url)
                await message.reply_text(caption)
            else:
                if img_url:
                    await message.reply_photo(photo=img_url, caption=caption)
                else:
                    await message.reply_text(caption)

        else:
            await message.reply_text(
                f"<b>Could not find any movie matching '{movie_name}'.</b>"
            )

    except Exception as e:
        await message.reply_text(f"Error occurred while searching: {e}")


@Client.on_message(filters.private & filters.command("get"))
async def movie_result_2(client, message):
    command_text = message.text.strip()

    if not command_text.startswith("/get "):
        await message.reply_text(
            "<b>Please use the command in the format: <code>/get The Gaelic King (2017)</code></b>"
        )
        return

    movie_name = command_text[5:].strip()

    if not movie_name:
        await message.reply_text(
            "<b>Please provide a movie name and year after the command.</b>"
        )
        return

    if BASE_URL + WEEK_RELEASES_PATH in movie_name:
        await message.reply_text(
            "<b>Use /scrap command to Scrap the Links from Page</b>"
        )
        return

    user_id = message.from_user.id

    try:
        movie_docs = await db.search_movie(movie_name)

        if movie_docs:

            img_url = movie_docs[0].get("img_url", None)

            links = [(doc.get("name"), doc.get("link")) for doc in movie_docs]

            if img_url:
                caption = f"<b>Query: {movie_name}</b>\n\n"
                caption += f"<b>Image URL:\n<code>{img_url}</code></b>\n\n"
            else:
                caption = f"<b>Movie found, but no image available for '{movie_name}'.</b>\n\n"

            if links:
                caption += "<b>Available Torrent Links:</b>"
                for name, link in links:
                    caption += (
                        f"<b>\n\n🗳 {name}\n\n🧲 • <code>{BASE_URL+link}</code></b>"
                    )
            else:
                caption += "<b>\n\nNo links available.</b>"

            caption += "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

            if len(caption) > 1000:
                if img_url:
                    await message.reply_photo(photo=img_url)
                await message.reply_text(caption)
            else:
                if img_url:
                    await message.reply_photo(photo=img_url, caption=caption)
                else:
                    await message.reply_text(caption)

        else:
            await message.reply_text(
                f"<b>Could not find any movie matching '{movie_name}'.</b>"
            )

    except Exception as e:
        await message.reply_text(f"Error occurred while searching: {e}")


user_pagination = {}


@Client.on_message(filters.private & filters.command("list"))
async def list_documents(client, message):
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


async def send_initial_document(client, message, user_id, index):
    documents = user_pagination[user_id]["documents"]
    document = documents[index]
    img_url = document.get("img_url", None)
    movie_name = document.get("text", "Unknown Movie")

    buttons = [
        InlineKeyboardButton("❌", callback_data="delete"),
        InlineKeyboardButton("➡️", callback_data=f"next_{user_id}_{index + 1}"),
    ]

    reply_markup = InlineKeyboardMarkup([buttons])

    caption = document.get("caption", "No Caption")
    caption += "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

    if img_url:
        await message.reply_photo(
            photo=img_url, caption=caption, reply_markup=reply_markup
        )
    else:
        await message.reply_text(caption, reply_markup=reply_markup)


async def show_document(client, message, user_id, index):
    documents = user_pagination[user_id]["documents"]
    document = documents[index]
    img_url = document.get("img_url", None)
    movie_name = document.get("text", "Unknown Movie")

    buttons = []
    if index > 0:
        buttons.append(
            InlineKeyboardButton("⬅️", callback_data=f"prev_{user_id}_{index - 1}")
        )
    buttons.append(InlineKeyboardButton("❌", callback_data="delete"))
    if index < len(documents) - 1:
        buttons.append(
            InlineKeyboardButton("➡️", callback_data=f"next_{user_id}_{index + 1}")
        )

    reply_markup = InlineKeyboardMarkup([buttons])

    caption = document.get("caption", "No Caption")
    caption += "\n\n<b><blockquote>〽️ Powered by @MOVIES_ADDDDA</blockquote></b>"

    if img_url:
        try:
            await client.edit_message_media(
                chat_id=message.chat.id,
                message_id=message.id,
                media=InputMediaPhoto(media=img_url, caption=caption),
                reply_markup=reply_markup,
            )
        except Exception as e:
            await message.reply_text(f"Error editing media: {e}")
    else:
        try:
            await client.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.id,
                caption=caption,
                reply_markup=reply_markup,
            )
        except Exception as e:
            await message.reply_text(f"Error editing caption: {e}")


def split_caption(document):
    """Split the caption into multiple parts if it exceeds the character limit."""
    caption = f"<b>Title: {document.get('name', 'Unknown Movie')}</b>\n\n"

    img_url = document.get("img_url", None)
    if img_url:
        caption += f"<b>Image URL:\n<code>{img_url}</code></b>\n\n"
    else:
        caption += "<b>No image available.</b>\n\n"

    link_name = document.get("name", "No Name")
    link_url = document.get("link", "No Link")
    caption += f"<b>Available Torrent Link:\n\n🗳 {link_name}\n\n🧲 • <code>{BASE_URL+link_url}</code>\n</b>"

    max_caption_length = 1000
    caption_parts = []
    while len(caption) > max_caption_length:
        split_point = caption[:max_caption_length].rfind("\n")
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
