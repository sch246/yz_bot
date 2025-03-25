'''
设定当检测到什么消息时回复什么，与命令的触发相独立
'''

import re
import os
import traceback
from main import data

from .py import *


def run(body:str):
    '''判断收到的消息，通过则进行处理，优先级比默认命令低，需要分条发送，
格式:
.link
    : (py|re) <name>[ while[ <other_name> (succ|fail)]+]
        : \\n <cond> \\n===\\n <action>
        | \\n <cond> || <action>
        | || <cond> || <action>
    | get <name>
    | del <name>
    | list
    | catch
        : <text>
        | || <text>

link, 每个link可被命名, 由cond和action构成, 当cond被判断并通过时action将被执行
创建link时, 可以指定是默认还是自定义
    默认则是插入到列表中(依旧需要命名)
    自定义则是决定在其它某个cond通过或失败时, 触发本cond的判定(好像有bug)
cond和action都是可执行的python代码, 但是环境不同, 执行流程是以links的第一个link作为开始，对每个link，判断cond，若成功则执行action然后依次执行succ内的links，失败则依次执行fail内的links, 前面的变量能传到后面，在执行整个links的过程中借用的是.py的globals和locals
    cond不应该使用sendmsg和recvmsg, 也不应该主动触发action, 它只能获得bot的状态, 传入的消息, 以及前面的cond留下的变量, 最终由最后一行表达式来判断通过与否

使用catch可以获取一个消息能触发哪些link
虽然链接是列表存储的，但是在fail或者succ列表中放重复的值会引起难以预料的后果
    每个link都会有2个列表，succ和fail
所有links放在一个列表内，当进来一条新消息时，默认测试最开头的那个link(最新创建的)
创建link时，需要设定while，while就是反向连接
    当指定的link成功或者失败时，运行本link
再然后，如果不设定while
    那么就是默认情况，会放到列表开头，并且将fail指向原来的开头link
    这样会形成列表结构
'''
    msg = cache.thismsg()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.link'):
            return '权限不足(一定消息内将不再提醒)'
        return

    body = cq.unescape(body)
    lines = body.splitlines()
    while len(lines)<2:
        lines.append('')
    first_line, *last_lines = lines

    s, last = read_params(first_line)

    if s in ['re','py']:
        name, _while, whiles = read_params(last, 2)
        if _while and _while != 'while':
            # 设置了错误的参数
            return run.__doc__
        elif not _while:
            # 空参数
            params = None
        else:
            # 否则得到[]或者[...]
            params = _params_from_whiles(whiles)
            if (s:=check_while(name, params)):
                return s

        link = get_link(name)
        if params==[]:
            # 清空前确认
            if not link:
                reply = yield '正在创建新link:\nwhile为空将无法被触发\n输入"y"以继续'
                if not (is_msg(reply) and reply['message'].strip()=='y'):
                    return '操作终止'
            elif links[0]['name']!=link[name]:
                reply = yield '正在编辑已有link:\n不在links开头且while为空将无法被触发\n输入"y"以继续'
                if not (is_msg(reply) and reply['message'].strip()=='y'):
                    return '操作终止'
        cond_and_action = '\n'.join(last_lines).split('\n===\n')
        return _set(name, s, link, params, cond_and_action)
    elif s=='del':
        name, last = read_params(last)
        return _del(name)
    elif s=='get':
        name, last = read_params(last)
        return _get(name)
    elif s=='list':
        return _list()
    elif s=='catch':
        if last.lstrip():
            reply = {**cache.thismsg(), 'message':last.lstrip()}
        else:
            reply = yield '输入想筛选的文本'
        return _catch(reply)
    return run.__doc__


def check_while(name, params):
    '''检查while后面的参数，通过返回None，不通过则返回字符串'''
    if len(params)%2==1:
        return 'while参数不成对'
    for i in range(len(params)//2):
        tar = params[2*i]
        if tar == name:
            return '递归达咩！'
        if not get_link(tar):
            return f'不存在的link:"{tar}"'
        connect_type = params[2*i+1]
        if connect_type not in ['succ','fail']:
            return f'期望得到"succ"或"fail"，但得到了"{connect_type}"'


def set_while(link, params):
    new = {'succ':[],'fail':[]}
    for i in range(len(params)//2):
        tar = params[2*i]
        connect_type = params[2*i+1]
        new[connect_type].append(tar)
    old = link['while']
    def _(connect_type):
        for tar in set((*old[connect_type],*new[connect_type])):
            if tar in old[connect_type] and tar not in new[connect_type]:
                disconnect_link(link['name'], tar, connect_type)
            elif tar in new[connect_type] and tar not in old[connect_type]:
                connect_link(link['name'], tar, connect_type)
    _('succ')
    _('fail')


def _set(name, type, link, params, parts):
    '''创建或修改link，需要分条发送
.link (py|re) <name>[ while[ <other_name> (succ|fail)]+]
    : \\n <cond> \\n===\\n <action>
    | \\n <cond> || <action>
    | || <cond> || <action>
使用py
    cond 和 action 是 python 代码，并共享 .py 的环境
    cond 会以最后一行作为表达式求布尔值作为判断依据，action则是无脑exec
    若打算 cond 无条件通过请使用 True 作为最后一行，否则使用 None 或者 以#开头 作为最后一行
使用re将会创建特殊的link，仅捕获文本消息
    cond将会作为特殊正则表达式，{name:type}会按照.py中str(type)作为正则表达式,捕获对应的字符串赋给name作为命名组
    type会寻找同名的变量，并转化为字符串插入
        若没有对应的变量或者不是合法的变量名，则作为字符串本身插入
        若type是字符串列表，则会变成(xx|xx|..)这样
    若name和type俱有，则以name创建命名组，type插入到命名组的匹配规则中
    若没有name，{:type}则直接插入
    若没有type，{name}则根据name本身进行判断，若name开头为大写字母则为'[\S\s]+'否则为'\S+'
    action使用{:name}进行替换，且最后一行作为表达式返回
    cond创建的命名组不进入.py的locals里
while 可以设置它在哪条 link 通过或未通过时执行
action 紧挨着 cond 成功时执行，原则上不允许 conds 使用 send,recv 和 do_action 等干涉自身的函数，这会影响到 catch 函数的准确性'''
    if parts and parts[0]:
        cond = parts.pop(0)
    else:
        reply = yield '输入cond'
        if not is_msg(reply):
            return '操作终止'
        cond = reply['message'].strip()
    if parts and parts[0]:
        action = parts.pop(0)
    else:
        reply = yield '输入action'
        if not is_msg(reply):
            return '操作终止'
        action = reply['message'].strip()

    # 如果编辑的是已有的link，遍历

    if link:
        return _edit(name, type, cond, action, params)
    else:
        return _add(name, type, cond, action, params)

def _set2(name, type, cond, action, whiles=None):
    '''while不输入则保持不变，设为''则清空链接，设为'a succ b fail'则设置链接'''
    # 弃用正则匹配
    cond = data.strip_re(cond)
    if (get_link(name)):
        # 如果已存在
        return _edit(name, type, cond, action, _params_from_whiles(whiles))
    else:
        # 如果添加
        return _add(name, type, cond, action, _params_from_whiles(whiles))

def _params_from_whiles(whiles=None):
    '''while不输入则保持不变，设为''则清空链接，设为'a succ b fail'则设置链接'''
    if whiles is None:
        return None
    elif whiles == '':
        return []
    else:
        return whiles.strip().split()

def _edit(name, type, cond, action, params=None):
    '''编辑已存在的链接
    params为None保持不变，设为[]以清空连接，设为['a', 'succ', 'b','fail',...]以设置连接'''
    link = get_link(name)
    if params is not None:
        set_while(link, params)
    link['type'] = type
    link['cond'] = cond
    link['action'] = action
    return '修改成功'


def _add(name, type, cond, action, params=None):
    '''添加链接
    params为None将无法触发，设为[]以清空连接，设为['a', 'succ', 'b','fail',...]以设置连接'''
    if params is None:
        # 默认链接
        links.insert(0,{
            'name':name,
            'type':type,
            'while':{'succ':[],'fail':[]},
            'cond':cond,
            'succ':[],
            'fail':[],
            'action':action,
        })
        if len(links)>1:
            connect_link(links[1]['name'], name, 'fail')
        return '创建成功'
    else:
        # 如果添加自定义连接
        links.append({
            'name':name,
            'type':type,
            'while':{'succ':[],'fail':[]},
            'cond':cond,
            'succ':[],
            'fail':[],
            'action':action,
        })
        if params!=[]:
            for i in range(len(params)//2):
                tar = params[2*i]
                connect_type = params[2*i+1]
                connect_link(name, tar, connect_type)
        return '创建成功'



def _del(name):
    '''删除link，如果有痕迹就也一并删了'''
    if del_link(name):
        return '删除成功'
    else:
        return '没有找到link'

def _get(name):
    '''列出link的type, cond和action'''
    link = get_link(name)
    if link:
        return formats_link(link, 1)
    else:
        return '没有找到link'

def _list():
    '''列出links的名字和它的指向
早:py
    fail
        啊这
啊这:re
    succ
        awa
    fail
        ww
类似这样'''
    lst = []
    for link in links:
        lst.append(formats_link(link))
    if lst:
        return pages.display(lst, 10)
    else:
        return 'links 为空'

def _catch(reply):
    '''根据输入的字符串，返回可能触发的link的名字'''
    names, ends = catch_links(reply)
    end = '\n终结于: '+' '.join(ends)
    if not names:
        return '该消息不触发任何link'+end
    return '触发的links: '+'\n'.join(names)+end



[
    {
        'name':'name',
        'while':{
            'succ':[],
            'fail':[]
        },
        'cond':'python code',
        'success':['linkname',...],
        'fail':['linkname',...],
        'action':'python code',
    },
    ...
]
