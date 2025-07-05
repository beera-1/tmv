import os, asyncio
from configs import *
from aiohttp import web
from logger import logger
import sys
from pyrogram import Client
import pyrogram
from utilities import web_server, ping_server, stop_user, ping_main_server
import pyrogram.utils

pyrogram.utils.MIN_CHAT_ID = -999999999999
pyrogram.utils.MIN_CHANNEL_ID = -10099999999999900


class ShortnerBot(Client):
    def __init__(self):
        super().__init__(
            "Scrapper",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="plugins"),
            workers=100,
        )

    async def start(self):                
        app = web.AppRunner(await web_server())
        await app.setup()
        ba = os.getenv("HOST", "0.0.0")
        port = int(os.getenv("PORT", 8080))
        await web.TCPSite(app, ba, port).start()
        await super().start()
        asyncio.create_task(ping_main_server())
        logger.info("Bot started successfully..")
        await self.send_message(GROUP_ID, "Bot Started")
        test_msg = await self.send_message(RSS_CHAT, "Bot Started")
        await test_msg.delete()
        asyncio.create_task(ping_server())

    async def stop(self, *args):
        await stop_user()
        await self.send_message(GROUP_ID, "Bot Stopped")
        test_msg = await self.send_message(RSS_CHAT, "Bot Stopped")
        await test_msg.delete()
        await super().stop()
        logger.info("Bot stopped..!")


if __name__ == "__main__":
    ShortnerBot().run()
