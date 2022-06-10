import atexit
from json import tool
import os
from yz.tool.tool import mkdirs, validateTitle,fmt,load_cq
import time
import yz.tool.data as data

def group_or_private(d:dict):
    return {k: d[k] for k in ['group_id','user_id'] if k in d.keys()}

class Logger():
    base_path = './log/'
    def __init__(self) -> None:
        self.log = {
            'group':{},
            'private':{},
            'other':{}
        }
        self.refresh_starttime()
        self.fmt = fmt('rb')+'storage> '
        atexit.register(self.save)
    def prn(self,text):
        print(self.fmt+text+fmt())

    def setbot(self,bot):
        self.bot = bot
    
    def refresh_starttime(self):
        self.starttime=time.strftime("%H:%M:%S", time.localtime(time.time()))

    def _save_lines(self,dirpath, str_list, endtime, head=data.default_head):
        
        head = head.replace('$start',self.starttime).replace('$end',endtime)
        
        dirpath = os.path.join(self.base_path, dirpath)
        mkdirs(dirpath)
        filepath =time.strftime("%Y-%m-%d.log", time.localtime())
        with open(os.path.join(dirpath, filepath),'a',encoding='utf-8') as f:
            f.write(head)
            f.writelines(str_list)


    def save(self):
        self.prn('logger> 保存中')
        endtime = time.strftime("%H:%M:%S", time.localtime(time.time()))
        for group_id, lines in self.log['group'].items():
            self._save_lines(os.path.join('group', str(group_id)), lines, endtime)
        for user_id, lines in self.log['private'].items():
            self._save_lines(os.path.join('private', str(user_id)), lines, endtime)
        self.refresh_starttime()
    
    def write(self, type, name, s):
        name = validateTitle(name)
        if not name in self.log[type].keys():
            self.log[type][name] = []
        self.log[type][name].append(s+'\n')
        
        
    def put(self, s, **kargs):
        '''[time], [group_id], [user_id], [pre]'''
        if 'time' in kargs.keys():
            t = kargs['time']
        else:
            t = time.time()
        time_head = time.strftime("[%H:%M:%S] ", time.localtime(t)) 
        if 'group_id' in kargs.keys():
            type='group'
            type_head = f"群聊> {kargs['group_id']} | "
            name = str(kargs['group_id']) + f"({self.bot.get_groupname(kargs['group_id'])})"
        elif 'user_id' in kargs.keys():
            type='private'
            type_head='私聊> '
            name= str(kargs['user_id']) + f"({self.bot.get_nickname(kargs['user_id'])})"
        else:
            type='other'
            type_head='其它>'
            name='0'
        if 'pre' in kargs.keys():
            s = kargs['pre'] + s
        print(type_head + s)
        self.write(type, name, time_head + s)


    def put_message(self, msg):
        '''[time], [group_id], [user_id], [pre], sender{nickname,user_id}, raw_message, message_id'''
        s = f"{msg['sender']['nickname']}({msg['sender']['user_id']}): {load_cq(msg['raw_message'])} ({msg['message_id']})"
        self.put(s,**msg)
    

    def put_action_before(self, action,params):
        pass
            
    
    def put_action(self, action,params,event):
        '''action和params是调用api发出的, event是接收到的'''
        # 若消息是发出的，则通过id获取消息信息
        
        if action in ['send_private_msg','send_group_msg','send_msg']:
            # 此时event仅包含message_id
            def put_msg(evt):
                '''以发送出去的信息的id查询所返回的信息'''
                msg={}
                msg.update(evt['data']) # 包含message, message_id, message_type, sender{nickname,user_id}, time
                s = f"{params['message']}({evt['data']['message_id']})"
                
                if 'group_id' in params.keys():
                    msg['group_id'] = params['group_id']
                    s = f"{self.bot.nickname}({self.bot.user_id}): " + s
                if 'user_id' in params.keys():
                    msg['user_id'] = params['user_id']
                    s = f"{self.bot.nickname}({self.bot.user_id}) >> {self.bot.get_nickname(params['user_id'])}({params['user_id']}): " + s
                self.put(s,**msg)
            self.bot.use_api('get_msg',echofunc=put_msg,log=False,message_id=event['data']['message_id'])