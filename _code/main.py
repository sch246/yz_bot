'''大概是用来启动bot的主程序，不过直接从这里启动的话，bot将失去重启功能'''
import re
import sys,os
import subprocess
from typing import Generator
import time
from typing import Any
from queue import Queue
from inspect import getgeneratorstate, GEN_CREATED
import random

from s3 import *
from s3 import __logging
# from s3.rag import hipporag
from s3.scheduler import scheduler
import s3.repl as repl
from s3.command_manager import CommandManager
import s3.config as config
import s3.counter as counter
from s3.add_attr import add_attr
import s3.file as file
# import s3.log as log
import s3.params as params
import s3.str_tool as str_tool
read_params = str_tool.read_params
import s3.thread as thread
from s3.thread import to_thread, ctrlc_decorator, SimpleFuture
from s3.delay_func import call_delay
import s3.mcrcon as mcrcon
from s3.cache_args import cache_args

import s3.linux_screen as screen
import s3.mc as mc
import s3.ident as ident
import s3.storage as storage

import s3.src as src


import bot.connect_with_http as connect
import bot.cmds as cmds
import bot.cq as cq
import bot.data as data

import bot.msgs as msgs
from bot.msgs import *
import bot.pages as pages
import bot.cache as cache
import bot.chatlog as chatlog

from s3.chat import (
        LLMCilent, Chat, sum_res, LLMResponse, resolve_model,
        get_cached_description, cache_description,
)
from s3.url_to_base64 import image_uri_to_data_uri, resolve_image_uri

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

llm_cilent = LLMCilent()

def gpt(settings, question):
        chat = Chat(chat_client=llm_cilent)
        chat.set_messages(settings)
        return sum_res(chat.chat(question)).content

description_dict = storage.get("llm_system", "description_cache")

def transform_image_to_text(text: str):
        '''
        将消息中的图片转换为描述
        多线程将图片转换为描述，然后合并
        '''
        lst = []
        for part in image_pattern.split(text):
                if image_pattern.match(part):
                        try:
                                data = cq.load(part)['data']
                                url = data['url']

                                description = get_cached_description(description_dict, url)
                                if description is not None:
                                        print(f"✅ 图片描述缓存命中: {description}")
                                        lst.append(description)
                                else:
                                        res = llm_cilent.get_vision_model()
                                        if res:
                                                print(f"❌ 图片描述缓存未命中: {url}")
                                                future = to_thread(llm_cilent.describe_image)(
                                                        url,
                                                        model=res,
                                                        convert_url=image_uri_to_data_uri,
                                                )
                                                print(f'future: {future}')
                                                lst.append((future, url))
                                        else:
                                                lst.append("[图片: 解析失败]")
                        except Exception as e:
                                traceback.print_exc()
                                lst.append("[图片: 解析失败]")
                elif part.strip():  # 只添加非空文本
                        lst.append(part)

        # 多线程（to_thread）将图片转换为描述
        resolved = []
        for item in lst:
                if isinstance(item, tuple) and isinstance(item[0], SimpleFuture):
                        description = item[0].result()
                        if description:
                                cache_description(description_dict, item[1], description)
                        resolved.append(f"[图片:{description or '解析失败'}]")
                else:
                        resolved.append(item)

        return ''.join(resolved)


__botdir__ = '/'.join(__file__.split('/')[:-2])

def msg_id(msg:dict):
        return (msg.get('group_id'), msg['user_id'])

def find(lst, f):
        '''返回None如果没有找到索引'''
        i=0
        for o in lst:
                if f(o):return i
                i += 1

def first_start():
        '''第一次加载'''
        config.init_config()
        print('未检测到config，第一次加载中')
        from random import randint
        check = randint(0, 9999)
        print(f'私聊bot验证码以确定master: {check:04d}')
        while True:
                msg = connect.recv_msg()
                if is_msg(msg) and msg['message'] == f'{check:04d}':
                        master = msg['user_id']
                        config.save_config([master],'ops')
                        send('已成为管理员', user_id=master)
                        break
        time.sleep(0.3)
        while True:
                send('请输入对bot的昵称，不要包含单引号', user_id=master)
                msg = connect.recv_msg()
                if not is_msg(msg) or not msg['user_id']==master:
                        continue
                name = msg['message'].strip()
                if "'" not in name:
                        config.save_config([name],'nicknames')
                        send(f'昵称已设置为【{name}】', user_id=master)
                        break
        time.sleep(0.3)
        send('设置完毕！', user_id=master)

def _init_self():
        while True:
                try:
                        login_info = connect.call_api('get_login_info')['data']
                        break
                except requests.exceptions.ConnectionError as e:
                        print(f'连接错误: {connect.post_url}')
                        print('3秒后重试...')
                        time.sleep(3)
        print("连接完成")
        # 加载设置
        if not os.path.isfile('config.json'):
                first_start()
        try:
                cache.ops_load()
                cache.nicknames_load()
        except KeyError:
                first_start()
        print("加载完成")

        qq, name = login_info['user_id'], login_info['nickname']
        cache.set('qq',qq)
        cache.set('name',name)
        cache.set('names',set([*cache.nicknames,name]))
        cache.update_user_name(qq, name)
        print(f'{name}({qq})启动了！')
        if '-a' in sys.argv[1:]:
                print('自动重启已开启')
        if '-d' in sys.argv[1:]:
                print('debug模式')

@call_delay(delay_secs=lambda *_,**__:random.uniform(-0.3, -0.6), max_size=20)
def send(text: Any, user_id: int | str = None, group_id: int | str = None, **params) -> int:
        '''user_id或者group_id是必须的'''
        debug('【准备发送消息】')
        text = str(text)

        # 转换魔术字符串
        text = text.replace('__botdir__',__botdir__)

        if 'message' in params.keys():
                # 防止message在下面的call_api撞车
                del params['message']
        if user_id is None and group_id is None:
                raise Exception('至少输入一个id!')
        if group_id:
                # 当仅同时传入group和user时保证是群聊
                user_id = None

        params['sender'] = {
                'user_id': cache.qq,
                'nickname': cache.name
        }

        call = connect.call_api('send_msg', message=text, user_id=user_id, group_id=group_id, **params)
        if not call['retcode'] == 0:
                print('发送消息失败 '+call['wording'])
                # send('发送消息失败\n'+call['wording'], user_id, group_id)
                return

        #------以下是获取自身发送的消息，并且记录下来------#

        call2 = connect.call_api('get_msg', message_id=call['data']['message_id'], user_id=user_id, group_id=group_id)
        if not call2['retcode'] == 0:
                print('获取发送的消息失败'+call2['wording'])
                return
        self_msg = call2['data']

        if group_id is None:
                self_msg['user_id'] = user_id
        else:
                self_msg['user_id'] = self_msg['sender']['user_id']

        print(f'[{time.strftime(r"%H:%M:%S")}]【发送消息】',end='')
        chatlog.write(self_msg)
        return call['data']['message_id']


i = 0
j = 0
k = 0

def _recv_time_counter():
        global i, j, k
        # 非常傻逼地用ijk作计时器，需要将心跳间隔设置为5s)
        # 虽然这玩意不准，不过用来大致度量时间还是可以的
        if i+j+k>0:
                print(str_tool.LASTLINE,end='')
        i += 1
        if i==12:
                j += 1
        if j==60:
                k += 1
        i %= 12
        j %= 60
        if j==0 and k==0:
                print(f'.{i}')
        elif j!=0 and k==0:
                print(f'|{j}.{i}')
        elif k!=0:
                print(f'█{k}|{j}.{i}')

reply_cq = re.compile(r'^(\[CQ:reply,[^\]]+\])([\S\s]*)')
def _strip_reply(msg):
        message = msg['message']
        msg['reply'] = None
        msg['at_cq'] = []
        m = reply_cq.match(message)
        if m:
                reply, message = m.groups()
                msg['reply'] = reply
        msg['message'] = message
        return msg



def _set_catches(value=None):
        msg = cache.thismsg()
        msg_loc = msg_id(msg)
        catches = cache.get('catches')
        if value is None:
                catches.pop(msg_loc, None)
        else:
                catches[msg_loc] = value

def _iter_ret(gen):
        '''获取generate的返回值，设置catches，并且发送msg过去'''
        try:
                if getgeneratorstate(gen)==GEN_CREATED:
                        _set_catches(gen)
                        return next(gen)
                else:
                        return gen.send(cache.thismsg())
        except StopIteration as e:
                _set_catches()
                return e.value

def _cmd_ret(ret):
        '''对命令返回值的处理'''
        if ret is None or ret=='':
                return
        elif isinstance(ret, Generator):
                _cmd_ret(_iter_ret(ret))
        else:
                sendmsg(ret)

def check_safe(command):
        res = gpt(['请你扮演一个linux命令检测函数，如果输入的命令是危险的，给出简短的危险警告信息，否则仅返回"True"'], command)
        if res=='True':
                return True, ''
        else:
                return False, res

def _run_bash(command, test=False):
        if not check_op_and_reply():
                return

        #is_safe, reason = check_safe(command)
        #if not is_safe:
        #        sendmsg(reason)
        #        return

        @to_thread
        def observer(cmd, timeout):
                cache.thismsg(cache.get_last())
                proc = subprocess.Popen(args=cmd,shell=True,encoding='utf-8'
                                , stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                        proc.wait(timeout)
                        s = cq.escape2(proc.stdout.read()).strip()
                        sendmsg(s)
                except subprocess.TimeoutExpired:
                        sendmsg('超时')
                        proc.kill()
                        proc.wait()
        if not test:
                observer(cq.unescape2(command), 10)
        else:
                sendmsg(f'执行了: {command}')

def recv(msg:dict|None):

        cmd_py = cmds.modules['py']

        if msg is None:
                print('连接已断开')
                time.sleep(1)
                return

        if is_heartbeat(msg):
                # _recv_time_counter()
                return

        global i, j, k
        i, j, k = 0, 0, 0

        if is_notify(msg) and msg['sub_type']=='input_status':
                #'post_type': 'notice', 'notice_type': 'notify', 'sub_type': 'input_status'
                return

        print(f'[{time.strftime(r"%H:%M:%S")}]【收到消息】',end='')

        # # 转换收到消息的图片为描述
        # if 'message' in msg:
        #         msg['message'] = transform_image_to_text(msg['message'])

        cache.thismsg(msg)
        cache.msgs['last'] = msg
        if not is_group_msg(msg) or msg not in nolog_groups:
                chatlog.write(msg)
        if any(c in sys.argv[1:] for c in ['-l','log_only']):
                return

        if is_msg(msg):
                chat_module = cmds.modules.get('chat')
                if chat_module:
                        chat_module.eager_cache_images(msg)
                msg = _strip_reply(msg)


        catches = cache.get('catches')
        msg_loc = msg_id(msg)
        if is_msg(msg) and msg['message'].startswith('^'):
                text = msg['message'][1:].rstrip()
                if text in 'Cc' and catches.get(msg_loc):
                        del catches[msg_loc]

        if catches.get(msg_loc):
                c = catches[msg_loc]
                if isinstance(c,Queue): # 在.py的input内使用
                        c.put(msg)
                        del catches[msg_loc]
                else:
                        _cmd_ret(c)
                return

        if is_msg(msg):
                text = msg['message']
                # 执行命令
                if text.startswith('.') and cmds.is_cmd(text[1:]):
                        _cmd_ret(cmds.run(*cmds.is_cmd(text[1:])))
                # 执行bash
                elif text.startswith('!'):
                        _run_bash(msg['message'][1:])
                # 测试执行bash
                elif text.startswith('#!'):
                        _run_bash(msg['message'][2:],test=True)
                elif cmd_py.links:
                        print('进入links')
                        cmd_py.exec_links(msg)
        elif is_notice(msg):
                if is_recall(msg):
                        _log = cache.getlog(msg)
                        recall_id = msg['message_id']
                        def _pop(m):
                                return is_msg(m) and recall_id==m.get('message_id')
                        m = find(_log, _pop)
                        if m:
                                _log.pop(m)
                elif cmd_py.links:
                        print('进入links2')
                        cmd_py.exec_links(msg)


print("加载funcs")
from funcs import *

if __name__=="__main__":
        print("启动中")
        _init_self()
        cmds.load()
        while True:
                try:
                        recv(connect.recv_msg())
                except KeyboardInterrupt:
                        print('bye.')
                        exit(0)
