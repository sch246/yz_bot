import asyncio
from json import tool
from yz.tool.tool import delay, stop_thread, arun, fmt, fms
import yz.tool.data as data
import json
from yz.tool.Logger import Logger
from yz.tool.Storage import Storage
import websockets
from yz.tool.config import load_config,save_config,init_or_load_config,config_file
import yz.tool.api as api
from yz.command.Command import Manager as CommandManager

class Bot:
    def __init__(self, storage:Storage, logger:Logger) -> None:
        # bot下主要是storage和logger
        # tool是工具, data是一些数据, config用来存取Config.json
        # api用来调用api(废话)
        # command是用来解析聊天发送的命令的，一般聊天不是命令
        self.storage=storage
        self.logger=logger
        self.CommandManager=CommandManager
        self.api = api
        self.websocket = None
        self.load_config()
        self._api_wait={}
        
        self.user_id = None
        self.nickname = None
        self.friends = {}
        self.groups = {}
        
        self.initfuncs=[
            self.load_login_info,
            self.load_friend_list,
            self.load_group_list
        ]
        self.storage.load_storage(self)
        self.logger.setbot(self)
    
    def get_nickname(self,user_id):
        return self.friends[user_id]['nickname']
    def get_groupname(self,group_id):
        return self.groups[group_id]['group_name']

    async def run(self):
        while True:
            print('尝试连接.. ')
            try:
                async with websockets.connect(data.uri) as websocket:
                    self.websocket = websocket
                    print(fms('initfunc> 运行初始化函数','gb'))
                    for func in self.initfuncs:
                        func(self)
                    print(fms('initfunc> 初始化完成','gb'))
                    try:
                        async for event in websocket:
                            self.recv_event(**json.loads(event))
                    except websockets.ConnectionClosed:
                        print('连接关闭', end=' > ')
                        continue
                    # while True:
                    #     self.recv_event(**json.loads(await websocket.recv()))
            except ConnectionRefusedError:
                print('连接被拒绝', end=' > ')
                continue


    def on_exit(self):
        self.logger.save()
        self.storage.save_storage()
        
    def load_config(self):
        print('加载 '+fms(config_file,'rg'),' : Bot')
        init = {
            'Bot':{
                'owner_id':'所有者的QQ号',
                'op_list':['管理员的QQ号'],
                'name':'柚子'
            }
        }
        dic = init_or_load_config(init)
        for key in dic['Bot'].keys():
            self.__dict__[key] = dic['Bot'][key]


    def load_friend_list(self,bot):
        def f(evt):
            self.friends = {user['user_id']: user for user in evt['data']}
            print(f'好友列表获取完成,共{len(evt["data"])}个')
        print('正在获取好友列表..')
        self.use_api('get_friend_list',f,False)

    def load_group_list(self,bot):
        def f(evt):
            self.groups = {group['group_id']: group for group in evt['data']}
            print(f'群列表获取完成,共{len(evt["data"])}个')
        print('正在获取群列表..')
        self.use_api('get_group_list',f,False)

    def load_login_info(self,bot):
        def f(evt):
            self.user_id=evt['data']['user_id']
            self.nickname=evt['data']['nickname']
            print(self.nickname,f'({self.user_id}), 已启动')
        print('正在获取登录信息..')
        self.use_api('get_login_info',f,False)
    
    def use_api(self, action, echofunc=lambda evt:None, log=True, **kargs):
        '''执行操作,正常情况下将记录操作并且不执行什么
        echofunc会在消息返回时调用
        '''
        if log:
            self.logger.put_action_before(action,kargs)
        def ef2(event):
            '''防止id重合(调用同样的函数)
            并且记录Log
            '''
            if log:
                self.logger.put_action(action,kargs,event)
            echofunc(event)
        f_id=id(ef2)
        self._api_wait[f_id]=ef2
        echo = f_id
        arun(self.websocket.send(json.dumps({
            'action': action,
            'echo': echo,
            'params': kargs
        })))
    
    def recv_event(self, **event):
        # TODO: 把更多的事件写进log
        keys = event.keys()
        if 'post_type' in keys and event['post_type'] == 'message':
            self.logger.put_message(event)
            if CommandManager.execute_if(self,event):
                return
        elif 'meta_event_type' in keys and event['meta_event_type'] == 'heartbeat':
            return
        elif 'status' in keys:
            if 'echo' in keys and event['echo'] in self._api_wait.keys():
                self._api_wait[event['echo']](event)
                del self._api_wait[event['echo']]
            else:
                print(f"echo> {event}")
        else:
            print(f"其它> {event}")
    