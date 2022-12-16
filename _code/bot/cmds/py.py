'''运行python代码的命令，是临时环境，重启后消失,除非最后一行以###开头'''
import traceback
import os, json, time, re, random, inspect
from queue import Queue
from inspect import currentframe

from main import *

try:
    exec(open('data/pyload.py', encoding='utf-8').read())
except:
    pass

def _input(s:str='',recv_all=False):
    q = Queue()
    msg_loc = msg_id(cache.thismsg())
    catches = cache.get('catches')
    catches[msg_loc] = q
    if s!='':
        sendmsg(s)
    r =  q.get(block=True)
    if not recv_all:
        if is_msg(r):
            return r['message']
        return
    return r

def _print(*values, sep=' ', end='\n', file=None,flush=False):
    if file is None:
        sendmsg(sep.join(map(str,values)))
    else:
        print(*values, sep, end, file, flush)

loc = {**globals()}
# 修改input和print
loc['input'] = _input
loc['print'] = _print


@to_thread
def run(body:str):
    '''运行python命令，在.py后空格或换行都行，最后一行的表达式若不是None或注释则会被返回
    默认情况下是临时环境，会在下一次重启后消失
    当最后一行以###开头时，代码将会在每次开启bot时被运行，注意不要写依赖于临时环境的代码
格式: .py <code:pycode>'''
    msg = cache.thismsg(cache.get_last())
    loc.update(globals())
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.py'):
            sendmsg('权限不足(一定消息内将不再提醒)')
        return
    body = cq.unescape(body.strip())
    if body=='':
        return run.__doc__
    lst = body.splitlines(True)
    try:
        exec(''.join(lst[:-1]), loc)
        last = lst[-1].strip()
        if last.startswith('###'):
            file.add('data/pyload.py', '\n'+body)
            sendmsg('添加成功')
        elif last.startswith('#'):
            return
        else:
            out = eval(last, loc)
            if out is not None:
                sendmsg(out)
    except:
        sendmsg(''.join(traceback.format_exc().splitlines(True)[3:]).strip())

#-----------------------------------------------
#----------------------------------------
def ls(obj):
    '''配合dir(), keys(), vars, __dict__等'''
    return '\n'.join(sorted(list(map(str,obj))))
def rd(r,d):
    '''掷骰子'''
    return sum(random.randint(1, d) for _ in range(r))

# 用于.link set2的捕获类型设置，举例: {a:Int}
Int = r'(?:0|-?[1-9]\d*)'
Name = r'\w+'
Param = r'\S+'
All = r'[\S\s]+'
CQ = r'\[CQ:[^,\]]+(?:,[^,=]+=[^,\]]+)*\]'

#----------------------------------------
#-----------------------------------------------

# 接收文件！
recv_file = cmds.modules['file']._recv_file
send_file = cmds.modules['file']._send_file
read_file = cmds.modules['file'].read_file
def write_file(path, value:str, start=None, end=None):
    lines = value.splitlines(True)
    cmds.modules['file'].write_file(path,lines,start,end)
    return '已写入 '+path
listdir = cmds.modules['file'].listdir

def same_times(f:Callable|str, i=None):
    '''用all筛选最近的i条消息，不包括本条消息，设为None则是筛选全部消息'''
    return cache.same_times(cache.thismsg(), f, i)
def any_same(f:Callable|str, i=None):
    '''用any筛选最近的i条消息，不包括本条消息，设为None则是筛选全部消息'''
    return cache.any_same(cache.thismsg(), f, i)
def get_one(f:Callable, i=None):
    '''获取最近的一条满足条件的消息，不包括本条消息'''
    return cache.get_one(cache.thismsg(), f, i)

# def recv_img(_msg, path):
#     if not is_img(_msg):
#         return '目标msg不是单个图片'
#     cmds.modules['file'].download(cq.load(_msg['message'])['data']['url'], path, msg)


# def send_img(path):
#     path = os.path.abspath(path)
#     cq_dict = {
#         'type':'image',
#         'data':{
#             'file':cq.escape(path)
#         }
#     }
#     sendmsg(cq.dump(cq_dict))




# links的部分


# 开头第一个即为link进入的点
links = storage.get('','links',list)



def lst_remove(lst, value):
    for i in range(len(lst)):
        i = -i-1
        if lst[i]==value:
            del lst[i]


def get_link(linkname:str):
    for link in links:
        if link['name']==linkname:
            return link
def del_link(linkname:str):
    for link in links:
        lst_remove(link['succ'],linkname)
        lst_remove(link['fail'], linkname)
        lst_remove(link['while']['succ'], linkname)
        lst_remove(link['while']['fail'], linkname)
    return _del_link(linkname)
def _del_link(linkname:str):
    '''只删本体不删联系'''
    for i in range(len(links)):
        i = -i-1
        if links[i]['name']==linkname:
            del links[i]
            return True
    return False
def set_link(linkname:str, d:dict):
    for link in links:
        if link['name']==linkname:
            link['cond'] = d['cond']
            link['succ'] = d['succ']
            link['fail'] = d['fail']
            link['action'] = d['action']
            return
    links.append({**d, 'name':linkname})

def connect_link(linkname:dict, tarlinkname:dict, connect_type:str):
    tarlink = get_link(tarlinkname)
    if tarlink and not linkname in tarlink[connect_type]:
        tarlink[connect_type].append(linkname)
    link = get_link(linkname)
    if link and not tarlinkname in link['while'][connect_type]:
        link['while'][connect_type].append(tarlinkname)
def disconnect_link(linkname:dict, tarlinkname:dict, connect_type:str):
    tarlink = get_link(tarlinkname)
    if tarlink and linkname in tarlink[connect_type]:
        lst_remove(tarlink[connect_type], linkname)
    link = get_link(linkname)
    if link and tarlinkname in link['while'][connect_type]:
        lst_remove(link['while'][connect_type], tarlinkname)

def run_or_remove(linklst:list):
    _del = []
    for linkname in linklst:
        _link = get_link(linkname)
        if not _link:
            _del.append(linkname)
            continue
        exec_link(_link)
    for linkname in _del:
        lst_remove(linklst,linkname)

# def setroot():
@to_thread
def exec_links():
    cache.thismsg(cache.get_last())
    loc.update(globals())
    exec_link(links[0])


def exec_link(link):
    name = link['name']
    type = link['type']
    cond = link['cond']
    succ = link['succ']
    fail = link['fail']
    action = link['action']
    if type=='py':
        out = exec_link_py(cond, action)
    else:
        out = exec_link_re(cond, action)
    if out:
        # 如果通过了，运行succ内的link，
        # succ内的link没找到则删掉
        print('触发了link:', name)
        run_or_remove(succ)
    else:
        # 如果没通过，运行fail内的link
        # fail内的link没找到则删掉
        run_or_remove(fail)


# exec py-------------------------------------------------

def exec_link_py(cond, action):
    code = cq.unescape(cond) # cond仅经过了一次strip
    if code=='':
        return
    lst = code.splitlines(True)
    out = None
    try:
        exec(''.join(lst[:-1]), loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, loc)

    except:
        sendmsg(''.join(traceback.format_exc().splitlines(True)[3:]))
    if out:
        _run_action_py(action, loc)
    return out



def _run_action_py(action, _loc):
    '''actions不需要捕获返回值'''
    if action=='':
        return
    action = cq.unescape(action)
    try:
        exec(action, _loc)
    except:
        sendmsg(''.join(traceback.format_exc().splitlines(True)[3:]))


# exec re-------------------------------------------------

# t = re.compile(r'({([^:}]*):([^}]+)}|{([^:}]+)})')

def exec_link_re(cond, action):
    msg = cache.thismsg()
    cond = cq.unescape(cond) # cond仅经过了一次strip
    if cond=='':
        return True
    if is_msg(msg):
        names = str_tool.stc_get(cond)(cq.unescape(msg['message']),{**globals(),**loc})
        if names is not None:
            _run_action_re(action, loc, names)
            return True



def _run_action_re(action, _loc, names:dict):
    '''actions不需要捕获返回值'''
    if action=='':
        return
    action = cq.unescape(action)
    action = str_tool.stc_set(action)(names)
    lst = action.splitlines(True)
    out = None
    try:
        exec(''.join(lst[:-1]), _loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, _loc)
        if out is not None:
            sendmsg(out)
    except:
        sendmsg(''.join(traceback.format_exc().splitlines(True)[3:]))

#-------------------------------------------------


def run_action(linkname, _loc, names={}):
    link = get_link(linkname)
    if link['type']=='py':
        _run_action_py(link['action'], _loc)
    else:
        _run_action_re(link['action'], _loc, names)
do_action = run_action

def formats_link(link:dict, mode=0):
    '''输出link的显示用字符串形式'''
    lst = [link['type']+' '+link['name']]
    if mode==0:
        if link['succ']:
            lst.append('    succ')
            lst.extend(map(lambda s:'        '+s, link['succ']))
        if link['fail']:
            lst.append('    fail')
            lst.extend(map(lambda s:'        '+s, link['fail']))
    elif mode==1:
        lst[0] += (' while'
            +''.join(f' {name} fail' for name in link['while']['fail'])
            +''.join(f' {name} succ' for name in link['while']['succ']))
        lst.append(link['cond'])
        lst.append('===')
        lst.append(link['action'])
    return '\n'.join(lst)



def catch_links(_msg):
    cache.thismsg(_msg)
    names = []
    ends = []
    catch_link(links[0], names, ends)
    return names, ends


def catch_link(link, names, ends):
    name = link['name']
    type = link['type']
    cond = link['cond']
    succ = link['succ']
    fail = link['fail']
    if type=='py':
        out = exec_link_py(cond, '')
    else:
        out = exec_link_re(cond, '')
    if out:
        names.append(name)
        if not succ:
            ends.append(name+':succ')
        for linkname in succ:
            _link = get_link(linkname)
            catch_link(_link, names, ends)
    else:
        if not fail:
            ends.append(name+':fail')
        for linkname in fail:
            _link = get_link(linkname)
            catch_link(_link, names, ends)
