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

CYBERLOOM_TIMEOUT = 20

CYBERLOOM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CYBERLOOM_HEADERS = {
    "User-Agent": CYBERLOOM_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}

# Your supplied HTML uses www.cyberloom.best
# Add other Cyberloom domains here if required.
CYBERLOOM_DOMAINS = (
    "cyberloom.best",
    "www.cyberloom.best",
)

MAX_CYBERLOOM_LINKS = 20


# ============================================================
# CYBERLOOM HELPERS
# ============================================================

def extract_urls(text):
    """
    Extract HTTP/HTTPS URLs from Telegram text.
    """

    if not text:
        return []

    urls = re.findall(
        r"https?://[^\s<>\"]+",
        text,
        flags=re.IGNORECASE,
    )

    cleaned = []

    for url in urls:
        # Remove common punctuation accidentally copied
        # after the URL.
        url = url.rstrip(
            ".,!?;:)]}>\"'"
        )

        if url:
            cleaned.append(url)

    return cleaned


def is_cyberloom_url(url):
    """
    Check whether the URL belongs to Cyberloom.
    """

    try:
        parsed = urllib.parse.urlparse(url)

        hostname = (
            parsed.hostname or ""
        ).lower()

        hostname = hostname.rstrip(".")

        return any(
            hostname == domain
            or hostname.endswith("." + domain)
            for domain in CYBERLOOM_DOMAINS
        )

    except Exception:
        return False


def get_base_url(url):
    """
    Return scheme + hostname.
    """

    parsed = urllib.parse.urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )


def decode_base64_url(value):
    """
    Decode normal or URL-safe Base64.
    """

    if not value:
        return None

    try:
        value = value.strip()

        # Fix missing Base64 padding.
        value += "=" * (
            -len(value) % 4
        )

        decoded = base64.urlsafe_b64decode(
            value
        ).decode(
            "utf-8",
            errors="ignore",
        )

        decoded = decoded.strip()

        if decoded.startswith(
            ("http://", "https://")
        ):
            return decoded

    except Exception as e:
        logger.debug(
            "Base64 decode failed: %s",
            e,
        )

    return None


def clean_cyberloom_title(title):
    """
    Clean website prefix from movie title.
    """

    if not title:
        return "Unknown Title"

    title = re.sub(
        r"^www\.[a-zA-Z0-9-]+\.[a-zA-Z]{2,}"
        r"\s*[-_:|]*\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title.strip(
        " -_:|"
    )


# ============================================================
# CYBERLOOM SYNC BYPASSER
# ============================================================

def bypass_cyberloom_sync(start_url):
    """
    Synchronous Cyberloom extractor.

    It follows:

        Cyberloom page
              ↓
             cta
              ↓
       /out?t=........
              ↓
        link/hash Base64
              ↓
        final file page
              ↓
       /api/link/{token}
              ↓
        direct download URL

    This function runs in a background thread.
    """

    session = requests.Session()

    session.headers.update(
        CYBERLOOM_HEADERS
    )

    try:

        # ====================================================
        # STEP 1
        # ====================================================

        logger.info(
            "Cyberloom Step 1: %s",
            start_url,
        )

        response1 = session.get(
            start_url,
            timeout=CYBERLOOM_TIMEOUT,
            allow_redirects=True,
        )

        response1.raise_for_status()

        soup1 = BeautifulSoup(
            response1.text,
            "html.parser",
        )

        # Your supplied HTML uses:
        #
        # <a id="cta" href="...">

        cta = soup1.find(
            "a",
            id="cta",
        )

        # Fallback for other versions.
        if not cta:
            cta = soup1.find(
                "a",
                id="continue-btn",
            )

        if not cta or not cta.get("href"):
            raise RuntimeError(
                "Cyberloom CTA/continue link was not found."
            )

        next_url = urllib.parse.urljoin(
            response1.url,
            cta["href"],
        )

        logger.info(
            "Cyberloom CTA: %s",
            next_url,
        )

        # ====================================================
        # STEP 2
        # ====================================================

        response2 = session.get(
            next_url,
            timeout=CYBERLOOM_TIMEOUT,
            allow_redirects=True,
        )

        response2.raise_for_status()

        messy_link = None

        # Your original code expects:
        #
        # var link = 'BASE64';
        # OR
        # var hash = 'BASE64';

        match = re.search(
            r"var\s+"
            r"(?:link|hash)"
            r"\s*=\s*"
            r"['\"]([^'\"]+)['\"]",
            response2.text,
            flags=re.IGNORECASE,
        )

        if match:

            messy_link = decode_base64_url(
                match.group(1)
            )

        # ====================================================
        # STEP 2 FALLBACK
        # ====================================================

        if not messy_link:

            soup2 = BeautifulSoup(
                response2.text,
                "html.parser",
            )

            continue_button = soup2.find(
                "a",
                id="continue-btn",
            )

            if (
                continue_button
                and continue_button.get("href")
            ):

                messy_link = urllib.parse.urljoin(
                    response2.url,
                    continue_button["href"],
                )

        if not messy_link:

            raise RuntimeError(
                "Could not extract the final Cyberloom page."
            )

        logger.info(
            "Cyberloom final page: %s",
            messy_link,
        )

        # ====================================================
        # STEP 3
        # ====================================================

        response3 = session.get(
            messy_link,
            timeout=CYBERLOOM_TIMEOUT,
            allow_redirects=True,
        )

        response3.raise_for_status()

        soup3 = BeautifulSoup(
            response3.text,
            "html.parser",
        )

        # ====================================================
        # TITLE
        # ====================================================

        h1 = soup3.find("h1")

        if h1:

            raw_title = h1.get_text(
                " ",
                strip=True,
            )

        else:

            # Fallback to title tag.
            title_tag = soup3.find(
                "title"
            )

            raw_title = (
                title_tag.get_text(
                    " ",
                    strip=True,
                )
                if title_tag
                else "Unknown Title"
            )

        movie_title = clean_cyberloom_title(
            raw_title
        )

        # ====================================================
        # FILE SIZE
        # ====================================================

        size_match = re.search(
            r"(\d+(?:\.\d+)?\s*"
            r"(?:MB|GB|KB|TB))",
            response3.text,
            flags=re.IGNORECASE,
        )

        file_size = (
            size_match.group(1)
            if size_match
            else "N/A"
        )

        # ====================================================
        # DOWNLOAD LINKS
        # ====================================================

        base_url = get_base_url(
            messy_link
        )

        links = []

        for anchor in soup3.find_all(
            "a"
        ):

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

            # =================================================
            # TOKEN API
            # =================================================

            if token:

                try:

                    api_url = (
                        f"{base_url}/api/link/"
                        f"{token}"
                    )

                    api_headers = {
                        **CYBERLOOM_HEADERS,
                        "X-Requested-With":
                            "XMLHttpRequest",
                        "Referer":
                            messy_link,
                    }

                    api_response = session.get(
                        api_url,
                        headers=api_headers,
                        timeout=CYBERLOOM_TIMEOUT,
                    )

                    if api_response.ok:

                        try:
                            data = (
                                api_response.json()
                            )

                        except ValueError:
                            data = {}

                        if data.get(
                            "success"
                        ):

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
                                        f"t="
                                        f"{urllib.parse.quote(str(token_time))}"
                                    )

                except Exception as e:

                    logger.warning(
                        "Cyberloom API error: %s",
                        e,
                    )

            # =================================================
            # URL PARAMETER
            # =================================================

            elif "url=" in href:

                try:

                    parsed = (
                        urllib.parse.urlparse(
                            href
                        )
                    )

                    query = (
                        urllib.parse.parse_qs(
                            parsed.query
                        )
                    )

                    if query.get("url"):

                        final_url = (
                            urllib.parse.unquote(
                                query["url"][0]
                            )
                        )

                except Exception as e:

                    logger.debug(
                        "URL parameter error: %s",
                        e,
                    )

            # =================================================
            # DIRECT URL
            # =================================================

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

            # =================================================
            # SAVE LINK
            # =================================================

            if final_url:

                # Avoid duplicate links.
                duplicate = any(
                    item["url"]
                    == final_url
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
                >= MAX_CYBERLOOM_LINKS
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


# ============================================================
# ASYNC CYBERLOOM BYPASSER
# ============================================================

async def bypass_cyberloom(url):
    """
    Run blocking requests code outside
    the Pyrogram event loop.
    """

    return await asyncio.to_thread(
        bypass_cyberloom_sync,
        url,
    )


# ============================================================
# CYBERLOOM TELEGRAM RESULT
# ============================================================

def build_cyberloom_result(result):
    """
    Build Telegram text + buttons.
    """

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
        "<b>🎬 Cyberloom Bypassed</b>\n\n"
        f"<b>🎬 Title:</b> {title}\n"
        f"<b>📦 Size:</b> <code>{size}</code>\n\n"
    )

    buttons = []

    if not links:

        text += (
            "<b>❌ No download links found.</b>"
        )

        return text, None

    text += (
        "<b>📥 Available Servers:</b>\n\n"
    )

    for index, item in enumerate(
        links,
        start=1,
    ):

        label = item.get(
            "label",
            f"Server {index}",
        )

        url = item.get(
            "url"
        )

        if not url:
            continue

        safe_label = html.escape(
            label[:50]
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

    return (
        text,
        (
            InlineKeyboardMarkup(
                buttons
            )
            if buttons
            else None
        ),
    )


# ============================================================
# /CB COMMAND
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
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
            "<b>Cyberloom Bypasser</b>\n\n"
            "<b>Usage:</b>\n"
            "<code>/cb https://www.cyberloom.best/...</code>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    urls = extract_urls(
        parts[1]
    )

    cyberloom_urls = [
        url
        for url in urls
        if is_cyberloom_url(url)
    ]

    if not cyberloom_urls:

        await message.reply_text(
            "<b>❌ No valid Cyberloom link found.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # Remove duplicates while
    # preserving order.
    cyberloom_urls = list(
        dict.fromkeys(
            cyberloom_urls
        )
    )

    status = await message.reply_text(
        "<b>⚡ Cyberloom Bypass Started...</b>\n\n"
        "🔗 Opening link...\n"
        "⏳ Please wait...",
        parse_mode=enums.ParseMode.HTML,
    )

    all_results = []

    for index, url in enumerate(
        cyberloom_urls,
        start=1,
    ):

        try:

            await status.edit_text(
                (
                    "<b>⚡ Cyberloom Bypass</b>\n\n"
                    f"🔗 Processing link "
                    f"{index}/{len(cyberloom_urls)}...\n"
                    "⏳ Please wait..."
                ),
                parse_mode=enums.ParseMode.HTML,
            )

            result = await bypass_cyberloom(
                url
            )

            all_results.append(
                result
            )

        except Exception as e:

            logger.exception(
                "Cyberloom command failed"
            )

            all_results.append(
                {
                    "title":
                        "Bypass Failed",
                    "size":
                        "N/A",
                    "links":
                        [],
                    "source":
                        url,
                    "error":
                        str(e),
                }
            )

    # ========================================================
    # BUILD FINAL MESSAGE
    # ========================================================

    final_text = ""
    final_buttons = []

    for result in all_results:

        if result.get("error"):

            final_text += (
                "<b>❌ Cyberloom Bypass Failed</b>\n"
                f"<code>"
                f"{html.escape(result['error'])}"
                f"</code>\n\n"
            )

            continue

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

        final_text += (
            "<b>🎬 Cyberloom Bypassed</b>\n"
            f"<b>🎬 Title:</b> {title}\n"
            f"<b>📦 Size:</b> <code>{size}</code>\n\n"
        )

        if not links:

            final_text += (
                "<b>❌ No download links found.</b>\n\n"
            )

            continue

        final_text += (
            "<b>📥 Available Servers:</b>\n\n"
        )

        for link_index, item in enumerate(
            links,
            start=1,
        ):

            label = item.get(
                "label",
                f"Server {link_index}",
            )

            url = item.get(
                "url"
            )

            if not url:
                continue

            safe_label = html.escape(
                label[:50]
            )

            final_text += (
                f"<b>📡 {safe_label}</b>\n"
                f"<code>{html.escape(url)}</code>\n\n"
            )

            final_buttons.append(
                [
                    InlineKeyboardButton(
                        f"📥 {label[:30]}",
                        url=url,
                    )
                ]
            )

    if not final_text:

        final_text = (
            "<b>❌ Cyberloom bypass failed.</b>"
        )

    else:

        final_text += (
            "<blockquote>"
            "⚡ Powered by @MOVIES_ADDDDA"
            "</blockquote>"
        )

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

    except Exception as e:

        logger.exception(
            "Could not edit Cyberloom status"
        )

        await message.reply_text(
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


# ============================================================
# AUTO CYBERLOOM DETECTION
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "scrap",
            "get",
            "list",
            "total_scraps",
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

    # Remove duplicates.
    cyberloom_urls = list(
        dict.fromkeys(
            cyberloom_urls
        )
    )

    status = await message.reply_text(
        "<b>🔗 Cyberloom link detected!</b>\n\n"
        "⚡ Starting bypass...\n"
        "⏳ Please wait...",
        parse_mode=enums.ParseMode.HTML,
    )

    results = []

    for index, url in enumerate(
        cyberloom_urls,
        start=1,
    ):

        try:

            await status.edit_text(
                (
                    "<b>🔗 Cyberloom detected!</b>\n\n"
                    f"⚡ Processing "
                    f"{index}/{len(cyberloom_urls)}...\n"
                    "⏳ Please wait..."
                ),
                parse_mode=enums.ParseMode.HTML,
            )

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
                    "title":
                        "Bypass Failed",
                    "size":
                        "N/A",
                    "links":
                        [],
                    "source":
                        url,
                    "error":
                        str(e),
                }
            )

    # ========================================================
    # FINAL AUTO RESULT
    # ========================================================

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

        final_text += (
            "<b>🎬 Cyberloom Bypassed</b>\n"
            f"<b>🎬 Title:</b> {title}\n"
            f"<b>📦 Size:</b> <code>{size}</code>\n\n"
        )

        if not links:

            final_text += (
                "<b>❌ No download links found.</b>\n\n"
            )

            continue

        final_text += (
            "<b>📥 Available Servers:</b>\n\n"
        )

        for link_index, item in enumerate(
            links,
            start=1,
        ):

            label = item.get(
                "label",
                f"Server {link_index}",
            )

            url = item.get(
                "url"
            )

            if not url:
                continue

            final_text += (
                f"<b>📡 "
                f"{html.escape(label[:50])}"
                f"</b>\n"
                f"<code>"
                f"{html.escape(url)}"
                f"</code>\n\n"
            )

            final_buttons.append(
                [
                    InlineKeyboardButton(
                        f"📥 {label[:30]}",
                        url=url,
                    )
                ]
            )

    final_text += (
        "<blockquote>"
        "⚡ Powered by @MOVIES_ADDDDA"
        "</blockquote>"
    )

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

    except Exception as e:

        logger.exception(
            "Failed to edit automatic Cyberloom result"
        )

        await message.reply_text(
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


# ============================================================
# START
# ============================================================

@Client.on_message(
    filters.command("start")
    & filters.private
)
async def start_handler(c, m):

    try:

        id = m.from_user.id

        if not await db.is_present(id):

            await db.add_user(id)

            await c.send_message(
                chat_id=GROUP_ID,
                text=(
                    "<b>New User Started The Bot\n\n"
                    "User: "
                    f'<a href="tg://openmessage?user_id={id}">'
                    "View User"
                    "</a>\n\n"
                    f"User ID: {id}</b>"
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
                        url="https://t.me/MOVIES_ADDDDA",
                    ),
                    InlineKeyboardButton(
                        "Sᴜᴩᴩᴏʀᴛ",
                        url="https://t.me/MOVIES_ADDDDA",
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
    & filters.command("total_scraps")
)
async def link_count(c, m):

    user_id = m.from_user.id

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
    & filters.command(["scrap"])
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
            "<b>Please provide a page url after "
            "the command.</b>"
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
                f"<b>Unable to retrieve the content "
                f"for the provided link "
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
                        "name":
                            link_text,
                        "link":
                            link["href"],
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
                f"available for '{page_url}'."
                "</b>\n\n"
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
                        f"{link['name']}\n\n"
                        "🧲 • "
                        f"<code>{link['link']}</code>"
                        "</b>"
                    )

                if len(
                    caption
                    + chunk_text
                ) > 1024:

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
#
# IMPORTANT:
# Cyberloom /cb is excluded here.
# Auto Cyberloom handler is also separate.
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "list",
            "get",
            "cb",
            "start",
            "scrap",
            "total_scraps",
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

        movie_docs = (
            await db.search_movie(
                movie_name
            )
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
                        f"<b>\n\n🗳 {name}\n\n"
                        "🧲 • "
                        f"<code>{BASE_URL + link}</code>"
                        "</b>"
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
                f"<b>Could not find any movie "
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
            "<b>Please provide a movie name and year "
            "after the command.</b>"
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

        movie_docs = (
            await db.search_movie(
                movie_name
            )
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
                        f"<b>\n\n🗳 {name}\n\n"
                        "🧲 • "
                        f"<code>{BASE_URL + link}</code>"
                        "</b>"
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
                f"<b>Could not find any movie "
                f"matching '{movie_name}'.</b>"
            )

    except Exception as e:

        await message.reply_text(
            "Error occurred while searching: "
            f"{e}"
        )


# ============================================================
# LIST / PAGINATION
# ============================================================

user_pagination = {}


@Client.on_message(
    filters.private
    & filters.command("list")
)
async def list_documents(
    client,
    message,
):

    user_id = m_user_id = (
        message.from_user.id
    )

    user_pagination[
        user_id
    ] = {
        "current_index":
            0
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
        ),
    ]

    if index < len(
        documents
    ) - 1:

        buttons.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"next_{user_id}_"
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
                    f"prev_{user_id}_"
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
                    f"next_{user_id}_"
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

            await client.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.id,
                caption=caption,
                reply_markup=reply_markup,
            )

        except Exception as e:

            await message.reply_text(
                f"Error editing caption: {e}"
            )


# ============================================================
# SPLIT CAPTION
# ============================================================

def split_caption(document):

    caption = (
        f"<b>Title: "
        f"{document.get('name', 'Unknown Movie')}"
        "</b>\n\n"
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

        # Safety fallback if no newline exists.
        if split_point <= 0:
            split_point = (
                max_caption_length
            )

        caption_parts.append(
            {
                "caption":
                    caption[
                        :split_point
                    ],
                "img_url":
                    img_url,
                "text":
                    document.get(
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
            "caption":
                caption,
            "img_url":
                img_url,
            "text":
                document.get(
                    "name",
                    "Unknown Movie",
                ),
        }
    )

    return caption_parts
