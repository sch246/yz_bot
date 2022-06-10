import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg


class shutdown:
    level=4
    def run(bot, body: str, msg: dict):
        print('关闭中')
        Create_Msg(bot,**msg).send('关闭中')
        sys.exit()
