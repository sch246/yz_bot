
import json,random,requests,re

from bot.msgs import *
import bot.cache as cache

from main import storage, connect, cmds, cq, send, recv

#-----------------------------------------------
#----------------------------------------

# 用于.link re的捕获类型设置，举例: {a:Int}
Int = r'(?:0|-?[1-9]\d*)'
Name = r'\w+'
Param = r'\S+'
All = r'[\S\s]+'
CQ = r'\[CQ:[^,\]]+(?:,[^,=]+=[^,\]]+)*\]'
def CQ_at(qq):
        '''要获取bot的qq匹配可以用CQ_at(cache.qq)'''
        return fr'\[CQ:at,qq={qq}\]'
#----------------------------------------
#-----------------------------------------------

def match(s:str):
        '''判断当前的消息是否通过某正则表达式，当前消息必须为文本消息'''
        msg = cache.thismsg()
        if is_msg(msg):
                return re.match(s, msg['message'])

def getlog(i=None):
        '''获取这个聊天区域的消息列表，由于是cache存的，默认只会保存最多256条'''
        msg = cache.thismsg()
        if i is None:
                return cache.getlog(msg)
        else:
                return cache.getlog(msg)[i]

def sendmsg(text,**_msg):
        '''发送消息，可以省略后续参数，获取当前线程开启时的最后一条消息'''
        msg = cache.thismsg()
        if not _msg:
                _msg = msg
        send(text,**_msg)

def recvmsg(text, sender_id:int=None, private=None, **kws):
        '''不输入后面的参数时，默认是同一个人同一个位置的recv，否则可以设定对应的sender和group
        私聊想模拟群内，只需要加group_id=xx
        当在群内想模拟私聊时，需要设private为True'''
        msg = cache.thismsg()
        if sender_id is None:
                sender_id = msg['user_id']
        if private is True:
                msg = msg.copy()
                del msg['group_id']
        recv({**msg, 'user_id':sender_id, 'message':text,'sender':{'user_id': sender_id}, **kws})

def ensure_user_id(user_id):
        if user_id is None:
                return cache.thismsg()['user_id']
        return user_id


def getstorage(user_id=None):
        '''获取个人的存储字典'''
        return storage.get('users',str(ensure_user_id(user_id)))


def getname(user_id=None, group_id=None):
        '''获取名字，如果有设置名字就返回设置的名字，反正无论如何都会获得一个'''
        msg = cache.thismsg()
        if user_id is None:
                user_id = msg['user_id']
        if group_id is None and is_group_msg(msg):
                group_id = msg['group_id']
        name = storage.get('users',str(user_id)).get('name')
        if name:
                return name
        if is_group_msg(msg):
                _, name = cache.get_group_user_info(group_id, user_id)
        else:
                name = cache.get_user_name(user_id)
        return name

def setname(name, user_id=None):
        '''设置名字，将会把名字存进个人存储字典中，可以被获取名字的函数获取'''
        name = storage.get('users',str(ensure_user_id(user_id)))['name'] = name
        return name


def iter_idx(iterable, idx):
        i = 0
        for obj in iterable:
                if i==idx:
                        return obj
                i += 1

def msglog(i=0):
        '''按索引获取文本消息，不会获取到其它类型的信息，若索引超出范围则返回None
        通常来讲默认会返回本条消息(本条消息肯定是文本啦)'''
        return iter_idx(filter(is_msg, getlog()), i)['message']

def getran(lst:list, ret_idx=False):
        '''随机取出列表中的元素'''
        if lst:
                idx = random.randint(0, len(lst)-1)
                if ret_idx:
                        return idx, lst[idx]
                else:
                        return lst[idx]

def getint(s:str):
        try:
                return int(s)
        except:
                return

def getcmd(name:str):
        return cmds.modules[name]

def headshot(user_id=None):
        return cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={ensure_user_id(user_id)}&s=640')

def ensure_group_id(group_id):
        if group_id is None:
                group_id = cache.thismsg().get('group_id')
                if group_id is None:
                        raise ValueError('需要在群内发送或者输入群号以调用此函数!')
        return group_id

def getgroupstorage(group_id=None):
        '''获取群的存储字典，可能异常'''
        return storage.get('groups',str(ensure_group_id(group_id)))

def getgroupname(group_id=None):
        '''获取名字，如果有设置名字就返回设置的名字，可能异常'''
        group_id = ensure_group_id(group_id)
        name = storage.get('groups',str(group_id)).get('name')
        if name:
                return name
        else:
                return cache.get_group_name(group_id)

def setgroupname(name, group_id=None):
        '''设置名字，将会把名字存进群存储字典中，可以被获取名字的函数获取，可能异常'''
        storage.get('groups',str(ensure_group_id(group_id)))['name'] = name
        return name

def memberlist(group_id=None):
        reply = connect.call_api('get_group_member_list',group_id=ensure_group_id(group_id))
        if reply['retcode']!=0:
                raise Exception('群成员列表获取失败:\n'+reply['wording'])
        return reply['data']

def curl(url):
        return requests.get(url).text

def jcurl(url):
        return json.loads(curl(url))

def ls(obj):
    '''配合dir(), keys(), vars, __dict__等'''
    return '\n'.join(sorted(list(map(str,obj))))

def rd(r,d):
        '''掷骰子'''
        return sum(random.randint(1, d) for _ in range(r))



def check_op_and_reply(msg=None):
        if msg is None:
                msg = cache.thismsg()
        if msg['user_id'] in cache.ops:
                return True
        if not cache.any_same(msg, '!'):
                send('权限不足(一定消息内将不再提醒)', **msg)
        return False
