# 它可能被多个线程调用，大意了
import asyncio
import json
from typing import Any, Callable
from threading import Event
from s3.ident import Ident_getter, Box
from s3.thread import to_thread
from bot.msgs import is_api

from s3 import debug

uri = "ws://localhost:6700"
ws: Any      # websocket
stop = False
stopping = Event()
stopping.clear()

def stop_thread():
    global stop
    global stopping
    stop = True
    stopping.wait()

class EvtObj:
    evt_objs = Box()

    def __init__(self, evt_dict: dict) -> None:
        self.value = evt_dict
        self.del_self = EvtObj.evt_objs.add(self)

    def check(self, wait: 'Wait'):
        if wait.check(self.value):
            self.del_self()
            return True
        return False


class Wait:
    new_waits = Box()   # 存放新wait
    waits = Box()       # 存放所有的旧wait

    def __init__(self, condition: Callable) -> None:
        self.waiting = Event()
        self.waiting.clear()
        self.condition = condition
        self.ret: dict
        self.del_self = Wait.new_waits.add(self)

    def reply(self, value):
        self.ret = value
        self.waiting.set()

    def check(self, evt: dict):
        '''检查evt，若通过，则进行回复并把自身从对应的box里删除，同时也要删除对应的evt'''
        if self.condition(evt):
            self.del_self()
            self.reply(evt)
            return True
        return False

    def first_check(self):
        if not any(evt_obj.check(self) for evt_obj in EvtObj.evt_objs.get_iter()):
            # 如果没有一个evt符合，则转入总的waits
            self.del_self()
            self.del_self = Wait.waits.add(self)


async def _run():
    global ws
    import websockets
    try:
        async with websockets.connect(uri) as websocket:
            ws = websocket
            waiting_connect.set()
            try:
                async for evt_json in ws:
                    # 可能进来新的evt，同时进来了新的wait
                    evt = json.loads(evt_json)
                    debug('\n收到evt:', evt)
                    # 先让新的wait过一遍旧的evt_objs
                    for wait in Wait.new_waits.get_iter():
                        wait.first_check()
                    # 然后所有的waits过一遍新的evt
                    if not any(wait.check(evt) for wait in Wait.waits.get_iter()):
                        EvtObj(evt)
                    if stop:
                        stopping.set()
                        exit()
            except websockets.ConnectionClosed:
                print('连接关闭')
                return
    except ConnectionRefusedError:
        print('连接被拒绝')
        return



waiting_connect = Event()
waiting_connect.clear()
def start():
    thread = to_thread(asyncio.run, True)(_run())
    waiting_connect.wait()
    return thread

def recv(condition: Callable = lambda e: True):
    wait = Wait(condition)
    wait.waiting.wait()
    return wait.ret


_id_getter = Ident_getter()


def arun(coroutine):
    '''在非协程函数里调用协程函数'''
    try:
        coroutine.send(None)
    except StopIteration as e:
        return e.value


def call_api(action: str, **params):
    global ws
    '''调用api'''
    uid = _id_getter.get()
    arun(ws.send(json.dumps({
        'action': action,
        'echo': uid,
        'params': params
    })))

    debug('\ncall_api:', action)

    def condition(evt):
        if is_api(evt) and evt['echo'] == uid:
            _id_getter.ret(uid)
            debug('\ncall_api:', action, '<-', evt)
            return True
        return False
    return recv(condition)


