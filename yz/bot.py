from yz.tool.tool import delay, stop_thread
import yz.tool.data as data
import json
from yz.tool.Logger import Logger
from yz.tool.Storage import Storage
import websockets
from yz.tool.config import load_config,save_config,default_or_load_config
from yz.command.Command import Manager as CommandManager

class Bot:
    def __init__(self, storage:Storage, logger:Logger) -> None:
        self.storage=storage
        self.logger=logger
        self.websocket = None
        self.commands = {}
        self.exec_pool = []
        self.load_config()
        
    async def run(self):
        while True:
            print('尝试连接.. ')
            try:
                async with websockets.connect(data.uri) as websocket:
                    self.websocket = websocket
                    try:
                        async for event in websocket:
                            self.recv_event(**json.loads(event))
                    except websockets.ConnectionClosed:
                        print('连接关闭', end=' > ')
                        continue
            except ConnectionRefusedError:
                print('连接被拒绝', end=' > ')
                continue


    def on_exit(self):
        self.logger.save()
        self.storage.save_msg_locals()
        
    def load_config(self):
        init = {
            'Bot':{
                'owner_id':'所有者的QQ号',
                'op_list':['管理员的QQ号'],
                'name':['柚子']
            }
        }
        dic = default_or_load_config(init)
        for key in dic['Bot'].keys():
            self.__dict__[key] = dic['Bot'][key]
    
    def use_api(self, action, echo, **kargs):
        # TODO: 把执行的操作写进log
        try:
            self.websocket.send(json.dumps({
                'action': action,
                'echo': echo,
                'params': kargs
            })).send(None)
        except StopIteration:
            pass
    
    def recv_event(self, **event):
        # TODO: 把更多的事件写进log
        keys = event.keys()
        if 'post_type' in keys and event['post_type'] == 'message':
            self.logger.put(event)
            if CommandManager.execute_if(self,event):
                return
                
                # code = event['raw_message'][3:].strip()
                # thread = exec_msg(code, event)
                # delay(3, stop_thread, thread, TimeoutError, lambda:Msg.send(f'[CQ:reply,id={message_id}] Time out.'))
                # self.exec_pool.append(thread)
        elif 'meta_event_type' in keys and event['meta_event_type'] == 'heartbeat':
            return
        elif 'status' in keys:
            return
        else:
            print(f"其它> {event}")
    
    def _register_command(self, name:str,func):
        self.commands[name]=func