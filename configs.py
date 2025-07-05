from os import getenv as genv
from pyrogram import Client

API_ID = genv("API_ID", "")

API_HASH = genv("API_HASH", "")

BOT_TOKEN = genv("BOT_TOKEN", "")

SUPPORT_GROUP = genv("SUPPORT_GROUP", "")

UPDATES_CHANNEL = genv("UPDATES_CHANNEL", "")

GROUP_ID = int(genv("GROUP_ID", ""))

DATABASE_URL = genv(
    "DATABASE_URL",
    "mongodb+srv://tamilmv:tamilmv@cluster0.vma15.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
)

USER_SESSION_STRING = genv(
    "USER_SESSION_STRING",
    "",
)

SERVER_URL = genv("SERVER_URL", "")


RSS_CHAT = int(
    genv("RSS_CHAT", "")
)  # add the channel id where the torrent files need to be sent

BASE_URL = genv(
    "BASE_URL", ""
).lower()  # update the main domain if changed


# change the theme as you required


START_TXT = """<b>Hello {}, I am a Scrapper Bot!.
๏ I can scrap links from 1tamilmv and update it in Bot..
๏ Click on the help menu button below to get information about my commands.
๏ Powered By </b>"""


HELP_TXT = """Send any Movie Name and I will provide torrent links.

Available Commands

-> /get - To Get Torrent Link of that Movie
-> /list - To get last 10 Movie/Series details

Updates - ⍟ """

ABOUT_TXT = """<b>╔════❰ ᴍᴏᴠɪᴇꜱᴀᴅᴅᴀ Scrapper Bot ❱═══❍
║ ┏━━━━━━━━━❥
║ ┣ Mʏ ɴᴀᴍᴇ -> {}
║ ┣ Uᴘᴅᴀᴛᴇꜱ -> <a href="https://t.me/MOVIES_ADDDDA">••Bᴏᴛs••</a>
║ ┣ 𝖲ᴜᴘᴘᴏʀᴛ -> <a href="https://t.me/MOVIES_ADDDDA"> Bᴏᴛs Sᴜᴩᴩᴏʀᴛ</a>
║ ┣ ๏ Cʜᴇᴄᴋ ʜᴇʟᴘ ᴛᴏ ᴋɴᴏᴡ ᴍᴏʀᴇ.
║ ┗━━━━━━━━━❥
╚═════❰ @ ❱═════❍</b>"""


WEEK_RELEASES_PATH = genv(
    "RELEASES_PATH", "/index.php?/forums/topic/"
)  # dont change this
