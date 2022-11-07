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
格式: .link
    : set <name:str>[ while( <name2:str> (fail | succ))+] || <cond:pycode> || <action:pycode>
    | del <name:str>
    | (catch <example:str>)
使用catch可以获取一个消息能触发哪些link
虽然链接是列表存储的，但是在列表中放重复的值会引起难以预料的后果'''
    global msg
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

    m = re.match(r'set ([\S]+)( while (\S.+))?$', head)
    if m:
        return _set(m)

    m = re.match(r'del ([\S]+)$', head)
    if m:
        return _del(m)
    return run.__doc__



def _set(m):
    '''创建或修改link，需要分条发送
    格式: .link (set <name:str>[ while( <name2:str> (fail | succ))+] || <cond:pycode> || <action:pycode>)
    while 可以设置它在哪条 link 通过或未通过时执行
    cond 和 action 是 python 代码，并共享 .py 的环境
    cond 会以最后一行作为表达式求布尔值作为判断依据，action则是无脑exec
    若打算 cond 无条件通过请使用 True 作为最后一行，否则使用 None 或者 以#开头 作为最后一行
    action 紧挨着 cond 成功时执行，原则上不允许 conds 使用 send,recv 和 do_action 等干涉自身的函数，这会影响到 catch 函数的准确性'''

    name = m.group(1)
    if get_link(name):
        exist = True
    else:
        exist = False


    if m.group(3):
        extra_param = m.group(3).strip()
        params = extra_param.split(' ')
        if len(params)%2==1:
            return 'while参数不成对'
        for i in range(len(params)//2):
            tar = params[2*i-1]
            if tar == name:
                return '递归达咩！'
            if not get_link(tar):
                return f'不存在的link:"{tar}"'
            type = params[2*i]
            if type not in ['succ','fail']:
                return f'期望得到"succ"或"fail"，但得到了"{type}"'
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
            tar = params[2*i-1]
            type = params[2*i]
            new[type].append(tar)
        old = link['while']
        def _(type):
            for tar in set((*old[type],*new[type])):
                if tar in old[type] and tar not in new[type]:
                    disconnect_link(name, tar, type)
                elif tar in new[type] and tar not in old[type]:
                    connect_link(name, tar, type)
        _('succ')
        _('fail')
        link['cond'] = cond
        link['action'] = action
        return '修改成功'
    elif not params:
        links.insert(0,{
            'name':name,
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
            'while':{'succ':[],'fail':[]},
            'cond':cond,
            'succ':[],
            'fail':[],
            'action':action,
        })
        for i in range(len(params)//2):
            tar = params[2*i-1]
            type = params[2*i]
            connect_link(name, tar, type)
        return '创建成功'




def _del(m):
    '''删除link，如果有痕迹就也一并删了'''
    name = m.group(1)
    if del_link(name):
        return '删除成功'
    else:
        return '没有找到link'




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