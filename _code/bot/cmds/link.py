'''
link, 每个link可被命名, 由cond和action构成, 当cond被判断并通过时action将被执行
创建link时, 可以指定是默认还是自定义
    默认则是插入到列表中(依旧需要命名)
    自定义则是决定在其它某个cond通过或失败时, 触发本cond的判定
cond和action都是可执行的python代码, 但是环境不同, 执行流程是以links的第一个link作为开始，对每个link，判断cond，若成功则执行action然后依次执行succ内的links，失败则依次执行fail内的links, 前面的变量能传到后面，在执行整个links的过程中借用的是.py的globals和locals
    cond不应该使用sendmsg和recvmsg, 也不应该主动触发action, 它只能获得bot的状态, 传入的消息, 以及前面的cond留下的变量, 最终由最后一行表达式来判断通过与否
'''

import re
import os
import traceback

from .py import *


def run(body:str):
    '''判断收到的消息，通过则进行处理，优先级比默认命令低，需要分条发送，
格式:
.link (py|re) <name>[ while( <other_name> (succ|fail))+]
 || <cond>
 || <action>
.link
 : del <name>
 | get <name>
 | list
 | catch || <text>
使用catch可以获取一个消息能触发哪些link
虽然链接是列表存储的，但是在列表中放重复的值会引起难以预料的后果'''
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        if not cache.any_same(msg, '\.link'):
            return '权限不足(一定消息内将不再提醒)'
        return

    body = cq.unescape(body)
    lines = body.splitlines()
    if not lines:
        lines = ['']
    head = lines[0].strip()
    value = lines[1:]

    m = re.match(r'(re|py) ([\S]+)( while (\S.+))?$', head)
    if m:
        return _set(m)

    m = re.match(r'del ([\S]+)$', head)
    if m:
        return _del(m)

    m = re.match(r'get ([\S]+)$', head)
    if m:
        return _get(m)

    m = re.match(r'list[\s]*$', head)
    if m:
        return _list(m)

    m = re.match(r'catch$', head)
    if m:
        return _catch(m)
    return run.__doc__



def _set(m):
    '''创建或修改link，需要分条发送
    格式: .link ((py|re) <name:str>[ while( <name2:str> (fail | succ))+] || <cond:pycode> || <action:pycode>)
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

    type = m.group(1)
    name = m.group(2)
    if get_link(name):
        exist = True
    else:
        exist = False


    if m.group(4):
        extra_param = m.group(4).strip()
        params = extra_param.split(' ')
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
    else:
        params = []

    reply = yield '输入cond'
    if not is_msg(reply):
        return '操作终止'
    cond = reply['message'].strip()
    reply = yield '输入action'
    if not is_msg(reply):
        return '操作终止'
    action = reply['message'].strip()

    # 如果编辑的是已有的link，遍历

    if exist:
        link = get_link(name)
        new = {'succ':[],'fail':[]}
        for i in range(len(params)//2):
            tar = params[2*i]
            connect_type = params[2*i+1]
            new[connect_type].append(tar)
        old = link['while']
        def _(connect_type):
            for tar in set((*old[connect_type],*new[connect_type])):
                if tar in old[connect_type] and tar not in new[connect_type]:
                    disconnect_link(name, tar, connect_type)
                elif tar in new[connect_type] and tar not in old[connect_type]:
                    connect_link(name, tar, connect_type)
        _('succ')
        _('fail')
        link['type'] = type
        link['cond'] = cond
        link['action'] = action
        return '修改成功'
    elif not params:
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
        links.append({
            'name':name,
            'type':type,
            'while':{'succ':[],'fail':[]},
            'cond':cond,
            'succ':[],
            'fail':[],
            'action':action,
        })
        for i in range(len(params)//2):
            tar = params[2*i-1]
            connect_type = params[2*i]
            connect_link(name, tar, connect_type)
        return '创建成功'




def _del(m):
    '''删除link，如果有痕迹就也一并删了'''
    name = m.group(1)
    if del_link(name):
        return '删除成功'
    else:
        return '没有找到link'

def _get(m):
    '''列出link的type, cond和action'''
    name = m.group(1)
    link = get_link(name)
    if link:
        return formats_link(link, 1)
    else:
        return '没有找到link'

def _list(m):
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
        return '\n'.join(lst)
    else:
        return 'links 为空'

def _catch(m):
    '''根据输入的字符串，返回可能触发的link的名字'''
    reply = yield '输入想筛选的文本'
    names = catch_links(reply)
    if not names:
        return '该消息不触发任何link'
    return '触发的links: '+'\n'.join(names)



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