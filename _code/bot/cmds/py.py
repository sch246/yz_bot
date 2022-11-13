'''运行python代码的命令，是临时环境，重启后消失,除非最后一行以###开头'''
import traceback
import os, json, time, re, random

from main import *



try:
    exec(open('data/pyload.py', encoding='utf-8').read())
except:
    pass

loc = {}

@to_thread
def run(body:str):
    '''运行python命令，在.py后空格或换行都行，最后一行的表达式若不是None或注释则会被返回
    默认情况下是临时环境，会在下一次重启后消失
    当最后一行以###开头时，代码将会在每次开启bot时被运行，注意不要写依赖于临时环境的代码
格式: .py <code:pycode>'''
    global msg
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        if not cache.any_same(msg, '\.py'):
            send('权限不足(一定消息内将不再提醒)', **msg)
        return
    body = cq.unescape(body.strip())
    if body=='':
        return run.__doc__
    lst = body.splitlines(True)
    try:
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('###'):
            file.add('data/pyload.py', '\n'+body)
            send('添加成功', **msg)
        elif last.startswith('#'):
            return
        else:
            out = eval(last, globals(), loc)
            if out is not None:
                send(out, **msg)
    except:
        send(''.join(traceback.format_exc().splitlines(True)[3:]).strip(), **msg)


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
    return cache.same_times(cache.get_last(), f, i)
def any_same(f:Callable|str, i=None):
    '''用any筛选最近的i条消息，不包括本条消息，设为None则是筛选全部消息'''
    return cache.any_same(cache.get_last(), f, i)
def get_one(f:Callable, i=None):
    '''获取最近的一条满足条件的消息，不包括本条消息'''
    return cache.get_one(cache.get_last(), f, i)

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
    global msg
    msg = cache.get_last()
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
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, globals(), loc)

    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]))
    if out:
        _run_action_py(action, loc)
    return out



def _run_action_py(action, _loc):
    '''actions不需要捕获返回值'''
    if action=='':
        return
    action = cq.unescape(action)
    try:
        exec(action, globals(), _loc)
    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]))


# exec re-------------------------------------------------

# t = re.compile(r'({([^:}]*):([^}]+)}|{([^:}]+)})')

def exec_link_re(cond, action):
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
        exec(''.join(lst[:-1]), globals(), _loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, globals(), _loc)
        if out is not None:
            send(out, **msg)
    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]))

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
    lst = [link['name']+': '+link['type']]
    if mode==0:
        if link['succ']:
            lst.append('    succ')
            lst.extend(map(lambda s:'        '+s, link['succ']))
        if link['fail']:
            lst.append('    fail')
            lst.extend(map(lambda s:'        '+s, link['fail']))
    elif mode==1:
        lst.append('    cond')
        lst.append(str_tool.addtab(link['cond'],tab='        '))
        lst.append('    action')
        lst.append(str_tool.addtab(link['action'],tab='        '))
    return '\n'.join(lst)
