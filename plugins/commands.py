import asyncio
import base64
import html
import logging
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)

from database import db
from configs import *
from utilities import fetch


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# CYBERLOOM CONFIG
# ============================================================

REQUEST_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CYBERLOOM_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}

# Add/remove domains here if required.
CYBERLOOM_DOMAINS = (
    "cyberloom",
    "messycloud",
)

MAX_LINKS_PER_MOVIE = 20


# ============================================================
# CYBERLOOM HELPERS
# ============================================================

def clean_cyberloom_title(title: str) -> str:
    """Clean website prefix from title."""

    if not title:
        return "Unknown Title"

    title = re.sub(
        r"^www\.[a-zA-Z0-9-]+\.[a-zA-Z]{2,}\s*[-_:|]*\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title.strip(" -_:|")


def is_cyberloom_url(url: str) -> bool:
    """Check whether URL belongs to a Cyberloom/MessyCloud domain."""

    try:
        parsed = urllib.parse.urlparse(url)

        hostname = parsed.netloc.lower()

        if not hostname:
            return False

        return any(
            domain in hostname
            for domain in CYBERLOOM_DOMAINS
        )

    except Exception:
        return False


def extract_urls(text: str):
    """Extract HTTP/HTTPS URLs from text."""

    if not text:
        return []

    urls = re.findall(
        r"https?://[^\s<>\"]+",
        text,
        flags=re.IGNORECASE,
    )

    # Remove Telegram/normal punctuation accidentally attached
    # to URLs.
    cleaned = []

    for url in urls:
        url = url.rstrip(
            ".,!?;:)]}>\"'"
        )

        if url:
            cleaned.append(url)

    return cleaned


def get_base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)

    return f"{parsed.scheme}://{parsed.netloc}"


def decode_base64(value: str):
    """Safely decode a base64 encoded URL."""

    try:
        value = value.strip()

        value += "=" * (
            -len(value) % 4
        )

        decoded = base64.urlsafe_b64decode(
            value
        ).decode(
            "utf-8",
            errors="ignore",
        )

        if decoded.startswith(
            ("http://", "https://")
        ):
            return decoded

    except Exception:
        pass

    return None


# ============================================================
# CYBERLOOM BYPASS
# ============================================================

def bypass_cyberloom_sync(start_url: str):
    """
    Synchronous Cyberloom extractor.

    This is executed through asyncio.to_thread()
    so requests does not block Pyrogram's event loop.
    """

    session = requests.Session()

    session.headers.update(
        CYBERLOOM_HEADERS
    )

    try:

        # ======================================================
        # STEP 1
        # ======================================================

        response1 = session.get(
            start_url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response1.raise_for_status()

        soup1 = BeautifulSoup(
            response1.text,
            "html.parser",
        )

        cta = soup1.find(
            "a",
            id="cta",
        )

        if not cta or not cta.get("href"):

            cta = soup1.find(
                "a",
                id="continue-btn",
            )

        if not cta or not cta.get("href"):

            raise RuntimeError(
                "Could not find Cyberloom continue link."
            )

        next_url = urllib.parse.urljoin(
            response1.url,
            cta["href"],
        )

        # ======================================================
        # STEP 2
        # ======================================================

        response2 = session.get(
            next_url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response2.raise_for_status()

        messy_link = None

        # Original Cyberloom format:
        #
        # var link = 'BASE64'
        # var hash = 'BASE64'

        match = re.search(
            r"var\s+(?:link|hash)\s*=\s*['\"]([^'\"]+)['\"]",
            response2.text,
            flags=re.IGNORECASE,
        )

        if match:

            messy_link = decode_base64(
                match.group(1)
            )

        # Fallback.
        if not messy_link:

            soup2 = BeautifulSoup(
                response2.text,
                "html.parser",
            )

            continue_btn = soup2.find(
                "a",
                id="continue-btn",
            )

            if (
                continue_btn
                and continue_btn.get("href")
            ):

                messy_link = urllib.parse.urljoin(
                    response2.url,
                    continue_btn["href"],
                )

        if not messy_link:

            raise RuntimeError(
                "Could not extract final Cyberloom page."
            )

        # ======================================================
        # STEP 3
        # ======================================================

        response3 = session.get(
            messy_link,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response3.raise_for_status()

        soup3 = BeautifulSoup(
            response3.text,
            "html.parser",
        )

        # ======================================================
        # TITLE
        # ======================================================

        h1 = soup3.find("h1")

        if h1:

            raw_title = h1.get_text(
                " ",
                strip=True,
            )

        else:

            raw_title = "Unknown Title"

        movie_title = clean_cyberloom_title(
            raw_title
        )

        # ======================================================
        # FILE SIZE
        # ======================================================

        size_match = re.search(
            r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB|TB))",
            response3.text,
            flags=re.IGNORECASE,
        )

        file_size = (
            size_match.group(1)
            if size_match
            else "N/A"
        )

        # ======================================================
        # DOWNLOAD LINKS
        # ======================================================

        base_url = get_base_url(
            messy_link
        )

        links = []

        for anchor in soup3.find_all("a"):

            label = anchor.get_text(
                " ",
                strip=True,
            )

            token = anchor.get(
                "data-token"
            )

            href = anchor.get(
                "href",
                "",
            )

            if not label:
                continue

            lower_label = label.lower()

            # Ignore website navigation.
            if any(
                word in lower_label
                for word in (
                    "login",
                    "home",
                    "back",
                    "messycloud",
                )
            ):
                continue

            final_url = None

            # ==================================================
            # TOKEN API
            # ==================================================

            if token:

                try:

                    api_url = (
                        f"{base_url}/api/link/{token}"
                    )

                    api_response = session.get(
                        api_url,
                        headers={
                            **CYBERLOOM_HEADERS,
                            "X-Requested-With":
                                "XMLHttpRequest",
                            "Referer":
                                messy_link,
                        },
                        timeout=REQUEST_TIMEOUT,
                    )

                    if api_response.ok:

                        data = api_response.json()

                        if data.get("success"):

                            final_url = data.get(
                                "url"
                            )

                            if final_url:

                                if not final_url.startswith(
                                    (
                                        "http://",
                                        "https://",
                                    )
                                ):

                                    final_url = (
                                        urllib.parse.urljoin(
                                            base_url,
                                            final_url,
                                        )
                                    )

                                token_time = data.get(
                                    "t"
                                )

                                if token_time:

                                    separator = (
                                        "&"
                                        if "?" in final_url
                                        else "?"
                                    )

                                    final_url += (
                                        f"{separator}"
                                        f"t={urllib.parse.quote(str(token_time))}"
                                    )

                except Exception as e:

                    logger.warning(
                        "Cyberloom API error: %s",
                        e,
                    )

            # ==================================================
            # URL PARAMETER
            # ==================================================

            elif "url=" in href:

                try:

                    parsed = urllib.parse.urlparse(
                        href
                    )

                    query = urllib.parse.parse_qs(
                        parsed.query
                    )

                    if query.get("url"):

                        final_url = (
                            urllib.parse.unquote(
                                query["url"][0]
                            )
                        )

                except Exception as e:

                    logger.warning(
                        "Cyberloom URL parsing error: %s",
                        e,
                    )

            # ==================================================
            # DIRECT URL
            # ==================================================

            elif href.startswith(
                (
                    "http://",
                    "https://",
                )
            ):

                if not is_cyberloom_url(
                    href
                ):

                    final_url = href

            # ==================================================
            # SAVE LINK
            # ==================================================

            if final_url:

                duplicate = any(
                    item["url"] == final_url
                    for item in links
                )

                if not duplicate:

                    links.append(
                        {
                            "label": label,
                            "url": final_url,
                        }
                    )

            if (
                len(links)
                >= MAX_LINKS_PER_MOVIE
            ):
                break

        return {
            "title": movie_title,
            "size": file_size,
            "links": links,
            "source": start_url,
        }

    except requests.Timeout:

        raise RuntimeError(
            "Cyberloom request timed out."
        )

    except requests.RequestException as e:

        raise RuntimeError(
            f"Cyberloom request failed: {e}"
        )

    finally:

        session.close()


async def bypass_cyberloom(url: str):
    """
    Async wrapper around the blocking extractor.
    """

    return await asyncio.to_thread(
        bypass_cyberloom_sync,
        url,
    )


# ============================================================
# CYBERLOOM TELEGRAM RESULT
# ============================================================

def build_cyberloom_result(result):

    title = html.escape(
        result.get(
            "title",
            "Unknown Title",
        )
    )

    size = html.escape(
        result.get(
            "size",
            "N/A",
        )
    )

    links = result.get(
        "links",
        [],
    )

    text = (
        f"<b>🎬 {title}</b>\n"
        f"<b>📦 Size:</b> "
        f"<code>{size}</code>\n\n"
    )

    if not links:

        text += (
            "<b>❌ No download links found.</b>"
        )

        return text, None

    buttons = []

    for index, link in enumerate(
        links,
        start=1,
    ):

        label = link.get(
            "label",
            f"Server {index}",
        )

        url = link.get(
            "url"
        )

        if not url:
            continue

        safe_label = html.escape(
            label[:40]
        )

        text += (
            f"<b>📡 {safe_label}</b>\n"
            f"<code>{html.escape(url)}</code>\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📥 {label[:30]}",
                    url=url,
                )
            ]
        )

    text += (
        "<blockquote>"
        "⚡ Cyberloom Bypassed Successfully"
        "</blockquote>"
    )

    return text, buttons


# ============================================================
# /CB COMMAND
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("cb")
)
async def cyberloom_command(
    client,
    message,
):

    command_text = (
        message.text or ""
    ).strip()

    parts = command_text.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.reply_text(
            "<b>Usage:</b>\n\n"
            "<code>/cb https://your-cyberloom-link</code>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    url_text = parts[1].strip()

    urls = extract_urls(
        url_text
    )

    if not urls:

        await message.reply_text(
            "<b>❌ Please provide a valid URL.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    cyberloom_urls = [
        url
        for url in urls
        if is_cyberloom_url(url)
    ]

    if not cyberloom_urls:

        await message.reply_text(
            "<b>❌ That doesn't appear to be a Cyberloom link.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    status = await message.reply_text(
        "<b>⚡ Cyberloom Bypass Started...</b>\n"
        "Please wait.",
        parse_mode=enums.ParseMode.HTML,
    )

    results = []

    for url in dict.fromkeys(
        cyberloom_urls
    ):

        try:

            result = await bypass_cyberloom(
                url
            )

            results.append(
                result
            )

        except Exception as e:

            logger.exception(
                "Cyberloom bypass failed"
            )

            results.append(
                {
                    "title": "Bypass Failed",
                    "size": "N/A",
                    "links": [],
                    "error": str(e),
                }
            )

    final_text = ""
    final_buttons = []

    for result in results:

        if result.get("error"):

            final_text += (
                "<b>❌ Cyberloom Bypass Failed</b>\n"
                f"<code>"
                f"{html.escape(result['error'])}"
                f"</code>\n\n"
            )

            continue

        text, buttons = (
            build_cyberloom_result(
                result
            )
        )

        final_text += (
            text + "\n\n"
        )

        if buttons:

            final_buttons.extend(
                buttons
            )

    try:

        await status.edit_text(
            final_text.strip(),
            reply_markup=(
                InlineKeyboardMarkup(
                    final_buttons
                )
                if final_buttons
                else None
            ),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception:

        logger.exception(
            "Failed to edit Cyberloom response"
        )


# ============================================================
# AUTO CYBERLOOM DETECTION
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "total_scraps",
            "scrap",
            "get",
            "list",
            "cb",
        ]
    )
)
async def cyberloom_auto_detect(
    client,
    message,
):

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    urls = extract_urls(
        text
    )

    if not urls:
        return

    cyberloom_urls = [
        url
        for url in urls
        if is_cyberloom_url(url)
    ]

    if not cyberloom_urls:
        return

    unique_urls = list(
        dict.fromkeys(
            cyberloom_urls
        )
    )

    status = await message.reply_text(
        "<b>🔗 Cyberloom link detected!</b>\n\n"
        "⚡ Bypassing...",
        parse_mode=enums.ParseMode.HTML,
    )

    results = []

    for url in unique_urls:

        try:

            result = await bypass_cyberloom(
                url
            )

            results.append(
                result
            )

        except Exception as e:

            logger.exception(
                "Automatic Cyberloom bypass failed"
            )

            results.append(
                {
                    "title": "Bypass Failed",
                    "size": "N/A",
                    "links": [],
                    "error": str(e),
                }
            )

    final_text = ""
    final_buttons = []

    for result in results:

        if result.get("error"):

            final_text += (
                "<b>❌ Cyberloom Bypass Failed</b>\n"
                f"<code>"
                f"{html.escape(result['error'])}"
                f"</code>\n\n"
            )

            continue

        text, buttons = (
            build_cyberloom_result(
                result
            )
        )

        final_text += (
            text + "\n\n"
        )

        if buttons:

            final_buttons.extend(
                buttons
            )

    final_text = final_text.strip()

    try:

        await status.edit_text(
            final_text,
            reply_markup=(
                InlineKeyboardMarkup(
                    final_buttons
                )
                if final_buttons
                else None
            ),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception:

        logger.exception(
            "Failed to edit automatic Cyberloom result"
        )


# ============================================================
# START
# ============================================================

@Client.on_message(
    filters.command("start")
    & filters.private
)
async def start_handler(
    c,
    m,
):

    try:

        user_id = m.from_user.id

        if not await db.is_present(
            user_id
        ):

            await db.add_user(
                user_id
            )

            await c.send_message(
                chat_id=GROUP_ID,
                text=(
                    "<b>New User Started The Bot\n\n"
                    f"User: "
                    f"<a href='tg://openmessage?"
                    f"user_id={user_id}'>"
                    f"View User</a>\n\n"
                    f"User ID: {user_id}</b>"
                ),
                parse_mode=enums.ParseMode.HTML,
            )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Hᴇʟᴩ Mᴇɴᴜ",
                        callback_data="help",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Cʜᴀɴɴᴇʟ",
                        url=(
                            "https://t.me/"
                            "MOVIES_ADDDDA"
                        ),
                    ),
                    InlineKeyboardButton(
                        "Sᴜᴩᴩᴏʀᴛ",
                        url=(
                            "https://t.me/"
                            "MOVIES_ADDDDA"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Cʟᴏsᴇ ❌",
                        callback_data="delete",
                    )
                ],
            ]
        )

        await m.reply_text(
            START_TXT.format(
                m.from_user.mention
            ),
            reply_markup=keyboard,
        )

    except Exception as e:

        await m.reply_text(
            f"Error: {e}"
        )


# ============================================================
# TOTAL SCRAPS
# ============================================================

@Client.on_message(
    filters.private
    & filters.command(
        "total_scraps"
    )
)
async def link_count(
    c,
    m,
):

    try:

        total_link_count = (
            await db.count_all_links()
        )

        msg = (
            "<b>📍Total Movies Scrapped : "
            f"<code>{total_link_count}</code>\n\n"
            "<blockquote>"
            "〽️ Powered by @MOVIES_ADDDDA"
            "</blockquote></b>"
        )

        await m.reply_text(
            msg
        )

    except Exception as e:

        await m.reply_text(
            f"🌶️ Error retrieving counts: {e}"
        )


# ============================================================
# SCRAP PAGE
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & filters.command("scrap")
)
async def page_scrap(
    client,
    message,
):

    page_url_msg = (
        message.text or ""
    ).strip()

    if not page_url_msg.startswith(
        "/scrap "
    ):

        await message.reply_text(
            "<b>Please use the command in the format: "
            "<code>/scrap link</code></b>"
        )

        return

    page_url = (
        page_url_msg[7:]
        .strip()
    )

    if not page_url:

        await message.reply_text(
            "<b>Please provide a page url "
            "after the command.</b>"
        )

        return

    if (
        BASE_URL
        + WEEK_RELEASES_PATH
        not in page_url
    ):

        await message.reply_text(
            "<b>Command is only used to scrap "
            "from 1Tamilmv Site</b>"
        )

        return

    try:

        page_html = await fetch(
            page_url
        )

        if not page_html:

            await message.reply_text(
                "<b>Unable to retrieve the "
                f"content for the provided link "
                f"'{page_url}'.</b>"
            )

            return

        soup = BeautifulSoup(
            page_html,
            "html.parser",
        )

        content_div = soup.find(
            "div",
            class_="ipsType_richText",
        )

        img_url = None

        if content_div:

            img_tag = content_div.find(
                "img"
            )

            if (
                img_tag
                and img_tag.get("src")
            ):

                img_url = img_tag[
                    "src"
                ]

        links = []

        for link in soup.find_all(
            "a",
            href=True,
        ):

            if (
                "attachment.php"
                in link["href"]
            ):

                link_text = (
                    link.get_text(
                        strip=True
                    )
                )

                links.append(
                    {
                        "name": link_text,
                        "link": link["href"],
                    }
                )

        if img_url:

            caption = (
                f"<b>Query : "
                f"<code>{page_url}</code></b>\n\n"
            )

            caption += (
                "Image URL:\n"
                f"<code>{img_url}</code>\n\n"
            )

        else:

            caption = (
                "<b>Movie found, but no image "
                f"available for '{page_url}'.</b>\n\n"
            )

        captions = []

        if links:

            caption += (
                "<b>Available Torrent Links:</b>"
            )

            for i in range(
                0,
                len(links),
                10,
            ):

                chunk = links[
                    i:i + 10
                ]

                chunk_text = ""

                for link in chunk:

                    chunk_text += (
                        f"<b>\n\n🗳 "
                        f"{link['name']}"
                        f"\n\n🧲 • "
                        f"<code>{link['link']}</code>"
                        f"</b>"
                    )

                if (
                    len(
                        caption
                        + chunk_text
                    )
                    > 1024
                ):

                    captions.append(
                        caption
                    )

                    caption = (
                        chunk_text
                    )

                else:

                    caption += (
                        chunk_text
                    )

            captions.append(
                caption
            )

        else:

            caption += (
                "<b>\n\nNo links available.</b>"
            )

            captions.append(
                caption
            )

        captions[-1] += (
            "\n\n"
            "<b><blockquote>"
            "〽️ Powered by @MOVIES_ADDDDA"
            "</blockquote></b>"
        )

        if img_url:

            await message.reply_photo(
                photo=img_url
            )

        for part in captions:

            await message.reply_text(
                part
            )

    except Exception as e:

        await message.reply_text(
            "Error occurred while processing: "
            f"{e}"
        )


# ============================================================
# MOVIE SEARCH
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "total_scraps",
            "scrap",
            "get",
            "list",
            "cb",
        ]
    )
)
async def movie_result_1(
    client,
    message,
):

    movie_name = (
        message.text or ""
    ).strip()

    if not movie_name:
        return

    # IMPORTANT:
    # Cyberloom links are handled above.
    if any(
        is_cyberloom_url(url)
        for url in extract_urls(movie_name)
    ):
        return

    if (
        BASE_URL
        + WEEK_RELEASES_PATH
        in movie_name
    ):

        await message.reply_text(
            "<b>Use /scrap command to Scrap "
            "the Links from Page</b>"
        )

        return

    try:

        movie_docs = await db.search_movie(
            movie_name
        )

        if movie_docs:

            img_url = movie_docs[
                0
            ].get(
                "img_url"
            )

            links = [
                (
                    doc.get("name"),
                    doc.get("link"),
                )
                for doc in movie_docs
            ]

            if img_url:

                caption = (
                    f"<b>Query: "
                    f"{movie_name}</b>\n\n"
                )

                caption += (
                    "<b>Image URL:\n"
                    f"<code>{img_url}</code></b>\n\n"
                )

            else:

                caption = (
                    "<b>Movie found, but no image "
                    f"available for '{movie_name}'."
                    "</b>\n\n"
                )

            if links:

                caption += (
                    "<b>Available Torrent Links:</b>"
                )

                for name, link in links:

                    caption += (
                        f"<b>\n\n🗳 {name}"
                        f"\n\n🧲 • "
                        f"<code>"
                        f"{BASE_URL + link}"
                        f"</code></b>"
                    )

            else:

                caption += (
                    "<b>\n\nNo links available.</b>"
                )

            caption += (
                "\n\n"
                "<b><blockquote>"
                "〽️ Powered by @MOVIES_ADDDDA"
                "</blockquote></b>"
            )

            if len(caption) > 1000:

                if img_url:

                    await message.reply_photo(
                        photo=img_url
                    )

                await message.reply_text(
                    caption
                )

            else:

                if img_url:

                    await message.reply_photo(
                        photo=img_url,
                        caption=caption,
                    )

                else:

                    await message.reply_text(
                        caption
                    )

        else:

            await message.reply_text(
                "<b>Could not find any movie "
                f"matching '{movie_name}'.</b>"
            )

    except Exception as e:

        await message.reply_text(
            "Error occurred while searching: "
            f"{e}"
        )


# ============================================================
# /GET
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("get")
)
async def movie_result_2(
    client,
    message,
):

    command_text = (
        message.text or ""
    ).strip()

    if not command_text.startswith(
        "/get "
    ):

        await message.reply_text(
            "<b>Please use the command in the format: "
            "<code>/get The Gaelic King (2017)"
            "</code></b>"
        )

        return

    movie_name = (
        command_text[5:]
        .strip()
    )

    if not movie_name:

        await message.reply_text(
            "<b>Please provide a movie name "
            "and year after the command.</b>"
        )

        return

    if (
        BASE_URL
        + WEEK_RELEASES_PATH
        in movie_name
    ):

        await message.reply_text(
            "<b>Use /scrap command to Scrap "
            "the Links from Page</b>"
        )

        return

    try:

        movie_docs = await db.search_movie(
            movie_name
        )

        if movie_docs:

            img_url = movie_docs[
                0
            ].get(
                "img_url"
            )

            links = [
                (
                    doc.get("name"),
                    doc.get("link"),
                )
                for doc in movie_docs
            ]

            if img_url:

                caption = (
                    f"<b>Query: "
                    f"{movie_name}</b>\n\n"
                )

                caption += (
                    "<b>Image URL:\n"
                    f"<code>{img_url}</code></b>\n\n"
                )

            else:

                caption = (
                    "<b>Movie found, but no image "
                    f"available for '{movie_name}'."
                    "</b>\n\n"
                )

            if links:

                caption += (
                    "<b>Available Torrent Links:</b>"
                )

                for name, link in links:

                    caption += (
                        f"<b>\n\n🗳 {name}"
                        f"\n\n🧲 • "
                        f"<code>"
                        f"{BASE_URL + link}"
                        f"</code></b>"
                    )

            else:

                caption += (
                    "<b>\n\nNo links available.</b>"
                )

            caption += (
                "\n\n"
                "<b><blockquote>"
                "〽️ Powered by @MOVIES_ADDDDA"
                "</blockquote></b>"
            )

            if len(caption) > 1000:

                if img_url:

                    await message.reply_photo(
                        photo=img_url
                    )

                await message.reply_text(
                    caption
                )

            else:

                if img_url:

                    await message.reply_photo(
                        photo=img_url,
                        caption=caption,
                    )

                else:

                    await message.reply_text(
                        caption
                    )

        else:

            await message.reply_text(
                "<b>Could not find any movie "
                f"matching '{movie_name}'.</b>"
            )

    except Exception as e:

        await message.reply_text(
            "Error occurred while searching: "
            f"{e}"
        )


# ============================================================
# PAGINATION
# ============================================================

user_pagination = {}


# ============================================================
# /LIST
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("list")
)
async def list_documents(
    client,
    message,
):

    user_id = message.from_user.id

    user_pagination[
        user_id
    ] = {
        "current_index": 0
    }

    try:

        documents = (
            await db.get_last_documents(
                40
            )
        )

        paginated_docs = []

        for document in documents:

            caption_parts = (
                split_caption(
                    document
                )
            )

            paginated_docs.extend(
                caption_parts
            )

        if paginated_docs:

            user_pagination[
                user_id
            ]["documents"] = (
                paginated_docs
            )

            await send_initial_document(
                client,
                message,
                user_id,
                0,
            )

        else:

            await message.reply_text(
                "<b>No documents found.</b>"
            )

    except Exception as e:

        await message.reply_text(
            f"Error retrieving documents: {e}"
        )


# ============================================================
# SEND INITIAL DOCUMENT
# ============================================================

async def send_initial_document(
    client,
    message,
    user_id,
    index,
):

    documents = (
        user_pagination[
            user_id
        ]["documents"]
    )

    document = documents[
        index
    ]

    img_url = document.get(
        "img_url"
    )

    buttons = [
        InlineKeyboardButton(
            "❌",
            callback_data="delete",
        )
    ]

    if index < len(
        documents
    ) - 1:

        buttons.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"next_"
                    f"{user_id}_"
                    f"{index + 1}"
                ),
            )
        )

    reply_markup = (
        InlineKeyboardMarkup(
            [buttons]
        )
    )

    caption = document.get(
        "caption",
        "No Caption",
    )

    caption += (
        "\n\n"
        "<b><blockquote>"
        "〽️ Powered by @MOVIES_ADDDDA"
        "</blockquote></b>"
    )

    if img_url:

        await message.reply_photo(
            photo=img_url,
            caption=caption,
            reply_markup=reply_markup,
        )

    else:

        await message.reply_text(
            caption,
            reply_markup=reply_markup,
        )


# ============================================================
# SHOW DOCUMENT
# ============================================================

async def show_document(
    client,
    message,
    user_id,
    index,
):

    documents = (
        user_pagination[
            user_id
        ]["documents"]
    )

    document = documents[
        index
    ]

    img_url = document.get(
        "img_url"
    )

    buttons = []

    if index > 0:

        buttons.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"prev_"
                    f"{user_id}_"
                    f"{index - 1}"
                ),
            )
        )

    buttons.append(
        InlineKeyboardButton(
            "❌",
            callback_data="delete",
        )
    )

    if index < len(
        documents
    ) - 1:

        buttons.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"next_"
                    f"{user_id}_"
                    f"{index + 1}"
                ),
            )
        )

    reply_markup = (
        InlineKeyboardMarkup(
            [buttons]
        )
    )

    caption = document.get(
        "caption",
        "No Caption",
    )

    caption += (
        "\n\n"
        "<b><blockquote>"
        "〽️ Powered by @MOVIES_ADDDDA"
        "</blockquote></b>"
    )

    if img_url:

        try:

            await client.edit_message_media(
                chat_id=message.chat.id,
                message_id=message.id,
                media=InputMediaPhoto(
                    media=img_url,
                    caption=caption,
                ),
                reply_markup=reply_markup,
            )

        except Exception as e:

            await message.reply_text(
                f"Error editing media: {e}"
            )

    else:

        try:

            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.id,
                text=caption,
                reply_markup=reply_markup,
            )

        except Exception as e:

            await message.reply_text(
                f"Error editing message: {e}"
            )


# ============================================================
# SPLIT CAPTION
# ============================================================

def split_caption(
    document
):

    caption = (
        f"<b>Title: "
        f"{document.get('name', 'Unknown Movie')}"
        f"</b>\n\n"
    )

    img_url = document.get(
        "img_url"
    )

    if img_url:

        caption += (
            "<b>Image URL:\n"
            f"<code>{img_url}</code></b>\n\n"
        )

    else:

        caption += (
            "<b>No image available.</b>\n\n"
        )

    link_name = document.get(
        "name",
        "No Name",
    )

    link_url = document.get(
        "link",
        "No Link",
    )

    caption += (
        "<b>Available Torrent Link:\n\n"
        f"🗳 {link_name}\n\n"
        "🧲 • "
        f"<code>{BASE_URL + link_url}</code>\n"
        "</b>"
    )

    max_caption_length = 1000

    caption_parts = []

    while len(caption) > max_caption_length:

        split_point = (
            caption[
                :max_caption_length
            ].rfind("\n")
        )

        if split_point <= 0:
            split_point = max_caption_length

        caption_parts.append(
            {
                "caption": caption[
                    :split_point
                ],
                "img_url": img_url,
                "text": document.get(
                    "name",
                    "Unknown Movie",
                ),
            }
        )

        caption = caption[
            split_point:
        ]

    caption_parts.append(
        {
            "caption": caption,
            "img_url": img_url,
            "text": document.get(
                "name",
                "Unknown Movie",
            ),
        }
    )

    return caption_parts
