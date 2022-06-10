import sys
import os
import atexit

class reboot:
    '''格式: .reboot
    用途: 重启bot'''
    level=4
    def run(bot, body: str, msg: dict):
        print('重启中')
        bot.api.Create_Msg(bot,**msg).send('重启中')
        def reboot_hello(bot):
            bot.api.Create_Msg(bot,**msg).send('重启完成')
        bot.storage.add_initfunc('reboot_hello',reboot_hello)
        atexit._run_exitfuncs()
        python = sys.executable
        os.execl(python, python, *sys.argv)
