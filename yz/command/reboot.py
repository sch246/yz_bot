import sys
import os


class reboot:
    level=4
    def run(bot, body: str, msg: dict):
        print('重启中')
        bot.api.Create_Msg(bot,**msg).send('重启中')
        def reboot_hello(bot):
            bot.api.Create_Msg(bot,**msg).send('重启完成')
        bot.storage.add_initfunc('reboot_hello',reboot_hello)
        bot.storage.save_storage()
        python = sys.executable
        os.execl(python, python, *sys.argv)
