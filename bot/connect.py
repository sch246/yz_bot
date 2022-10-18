'''websocket版本的connect'''
import asyncio
import json
import websockets
from typing import Any, Callable
from threading import Event
from s3.ident import Ident_getter, Box
from s3.thread import to_thread
from bot.msgs import is_api

from s3 import debug

uri = "ws://localhost:6700"
ws: Any      # websocket


class Evt:
    box = Box()

    def __init__(self, evt_dict: dict) -> None:
        self.value = evt_dict

    def when_recv(self):
        if all(not wait.check(self.value) for wait in Wait.box):
            # 如果没有一个wait符合，则转入总的evts
            self.del_self = Evt.box.add(self)

    def check(self, wait: 'Wait'):
        '''检查wait，若通过则将自身在evt_objs中删除'''
        if wait.condition(self.value):
            self.del_self()
            wait.reply(self.value)
            return True
        return False


class Wait:
    box = Box()       # 存放所有的旧wait
    ret: dict
    del_self: Callable

    def __init__(self, condition: Callable) -> None:
        self.waiting = Event()
        self.waiting.clear()
        self.condition = condition

    def reply(self, value):
        self.ret = value
        self.waiting.set()

    async def when_recv(self):
        if all(not evt_obj.check(self) for evt_obj in Evt.box):
            # 如果没有一个evt符合，则转入总的waits
            self.del_self = Wait.box.add(self)

    def check(self, evt: dict):
        '''检查evt，若通过，则进行回复并把自身从对应的box里删除'''
        if self.condition(evt):
            self.del_self()
            self.reply(evt)
            return True
        return False


loop: asyncio.AbstractEventLoop
stopping = Event()
stopping.clear()

async def _stop():
    stopping.set()
    exit()

def stop():
    global stopping
    global loop
    asyncio.run_coroutine_threadsafe(_stop(), loop)
    stopping.wait()

async def _run():
    global loop
    global ws
    loop = asyncio.get_running_loop()
    try:
        async with websockets.connect(uri) as websocket:
            ws = websocket
            waiting_connect.set()
            try:
                async for evt_json in ws:
                    # 进来新的evt，就过一遍所有的wait
                    # debug(' | len:', len(Evt.box))
                    Evt(json.loads(evt_json)).when_recv()
            except websockets.ConnectionClosed:
                print('连接关闭')
                return
    except ConnectionRefusedError:
        print('连接被拒绝')
        return


waiting_connect = Event()
waiting_connect.clear()


def start():
    global waiting_connect
    thread = to_thread(asyncio.run, True)(_run())
    waiting_connect.wait()
    return thread


def recv(condition: Callable = lambda e: True):
    global loop
    wait = Wait(condition)
    asyncio.run_coroutine_threadsafe(wait.when_recv(), loop)
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

    debug(' | ', action, ':', params)

    def condition(evt):
        if is_api(evt) and evt['echo'] == uid:
            _id_getter.ret(uid)
            debug(' √ ', action, '<-', evt)
            return True
        return False
    return recv(condition)

# import atexit

# @atexit.register
# def on_exit():
#     for wait in Wait.box:
#         wait.reply('exit')
