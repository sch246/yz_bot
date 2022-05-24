#!/usr/bin/env python
import asyncio
import os
import atexit


from yz.bot import Bot
from yz.tool.Storage import Storage
from yz.tool.Logger import Logger



if __name__ == "__main__":
    # os.chdir(os.path.dirname(__file__))
    bot = Bot(Storage(),Logger())
    atexit.register(bot.on_exit)
    asyncio.run(bot.run())

