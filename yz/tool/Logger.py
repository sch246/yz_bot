import os
from yz.tool.tool import mkdirs
import time
import yz.tool.data as data

class Logger():
    base_path = './log/'
    def __init__(self) -> None:
        self.log = {
            'group':{},
            'private':{}
        }


    def _save_lines(self,dirpath, str_list, head=data.default_head):
        dirpath = os.path.join(self.base_path, dirpath)
        mkdirs(dirpath)
        filepath =time.strftime("%Y-%m-%d.log", time.localtime())
        with open(os.path.join(dirpath, filepath),'a',encoding='utf-8') as f:
            f.write(head)
            f.writelines(str_list)
    
    def write(self, type, uid, s):
        if not uid in self.log[type].keys():
            self.log[type][uid] = []
        self.log[type][uid].append(s+'\n')


    def save(self):
        for group_id, lines in self.log['group'].items():
            self._save_lines(str(group_id), lines)
        for user_id, lines in self.log['private'].items():
            self._save_lines(str(user_id), lines)


    def put(self, msg:dict[str,any]):
        time_head = time.strftime("[%H:%M:%S] ", time.localtime(msg['time'])) 
        s = f"{msg['sender']['nickname']}({msg['user_id']}): {msg['raw_message']} ({msg['message_id']})"
        if 'group_id' in msg.keys():
            type='group'
            type_head = f"群聊> {msg['group_id']} | "
            key = 'group_id'
        elif 'user_id' in msg.keys():
            type='private'
            type_head='私聊> '
            key = 'user_id'
        print(type_head + s)
        self.write(type, msg[key], time_head + s)
