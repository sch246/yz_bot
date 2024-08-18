from urllib import parse
import json,random,requests,re
import inspect


from bot.msgs import *
import bot.cache as cache

from main import storage, connect, cmds, cq, send, recv, to_thread

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


def getstorage(user_id=None)->dict:
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
    return cmds.modules.get(name)

def headshot_url(user_id=None):
    return f'https://q2.qlogo.cn/headimg_dl?dst_uin={ensure_user_id(user_id)}&spec=100'
    return f'http://q1.qlogo.cn/g?b=qq&nk={ensure_user_id(user_id)}&s=640'
def headshot(user_id=None):
    return cq.url2cq(headshot_url(user_id))

def dict2url(d:dict):
    lst=[]
    for k, v in d.items():
        lst.append(f'{parse.quote(k)}={parse.quote(v)}')
    return '&'.join(lst)

def ensure_group_id(group_id):
    if group_id is None:
        group_id = cache.thismsg().get('group_id')
        if group_id is None:
            raise ValueError('需要在群内发送或者输入群号以调用此函数!')
    return group_id

def getgroupstorage(group_id=None)->dict:
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


def getchatstorage()->dict:
    '''获取当前聊天空间的storage'''
    if is_group_msg(cache.thismsg()):
        return getgroupstorage()
    else:
        return getstorage()


#-----------------------------------------------------------------
# chat
###
settings = storage.get('','settings',list)
###
def msg2name(msg=None):
    '''获取名字，如果有设置名字就返回设置的名字，反正无论如何都会获得一个'''
    if msg is None:
        msg = cache.thismsg()
    user_id = msg['user_id']
    group_id = msg.get('group_id')
    name = storage.get('users',str(user_id)).get('name')
    if name:
        return name
    if group_id:
        _, name = cache.get_group_user_info(group_id, user_id)
    else:
        name = cache.get_user_name(user_id)
    return name

def msgtext(msg=None):
    if msg is None:
        msg=cache.thismsg()
    # return f'[{msg2name(msg)}]({msg["user_id"]})'+''.join(['\n    '+line for line in msg['message'].splitlines()])
    return f'[{msg2name(msg)}]({msg["user_id"]}): '+msg['message']
###
chatting=False
###
def is_at(qq):
    def _(cqtext):
        d=cq.load(cqtext)
        return d['type']=='at' and not d['data']['qq']=='all' and int(d['data']['qq'])==qq
    return _
def has_at(qq):
    def _(msg):
        if not is_msg(msg): return False
        cqs=cq.find_all(msg['message'])
        return any(map(is_at(qq), cqs))
    return _
###
###
import base64
import os

def is_image_accessible(url):
    try:
        response = requests.get(url, stream=True)
        # 检查响应状态码是否为200
        if response.status_code == 200:
            # 检查内容类型是否为图片
            content_type = response.headers.get('Content-Type')
            if 'image' in content_type:
                return True
    except requests.RequestException as e:
        pass
    return False

# 初始化缓存字典
downloaded_files = {}

# 从文件夹中初始化缓存字典
tmp_files_dir = 'data/tmp_files_chat'
if not os.path.exists(tmp_files_dir):
    os.makedirs(tmp_files_dir)

for file_name in os.listdir(tmp_files_dir):
    if file_name.endswith(('.jpg', '.png', '.jpeg', '.gif')):
        file_path = os.path.join(tmp_files_dir, file_name)
        downloaded_files[file_name] = file_path

# 函数：下载图片
def download_img(picture_url, name):
    response = requests.get(picture_url)
    file_path = os.path.join(tmp_files_dir, name)
    with open(file_path, 'wb') as file:
        file.write(response.content)
    return file_path

# 获取文件的MIME类型
def get_mime_type(file_name):
    ext = file_name.split('.')[-1].lower()
    return {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif'
    }.get(ext, 'application/octet-stream')


def calculate_image_tokens(image_path:str | bytes):
    # 打开图片并获取尺寸
    with Image.open(image_path) as img:
        width, height = img.size

    # 默认情况下，512x512以下的图片消耗85 tokens
    if width <= 512 and height <= 512:
        return 85

    # 将图片缩放至2048x2048以内，保持宽高比
    max_side = 2048
    if max(width, height) > max_side:
        scale_ratio = max_side / max(width, height)
        width = int(width * scale_ratio)
        height = int(height * scale_ratio)

    # 将图片缩放至最短边为768像素，保持宽高比
    min_side = 768
    if min(width, height) < min_side:
        scale_ratio = min_side / min(width, height)
        width = int(width * scale_ratio)
        height = int(height * scale_ratio)

    # 计算512x512像素的块数
    num_squares = (width // 512) * (height // 512)
    if width % 512 != 0:
        num_squares += height // 512
    if height % 512 != 0:
        num_squares += width // 512
    if width % 512 != 0 and height % 512 != 0:
        num_squares += 1

    # 计算最终的token消耗
    return 170 * num_squares + 85


chat_picture_descriptions = storage.get('','chat_picture_descriptions')

# 正则表达式模式
image_pattern = re.compile(r'(\[CQ:image(?:,[^,=]+=[^,\]]*)*\])')
import traceback
from chat import Chat
chat_client = Chat()
# 分割消息并处理图片
def msg_split(text):
    lst = []
    for part in image_pattern.split(text):
        if image_pattern.match(part):
            try:
                # 假设cq.load(part)能正确提取图片URL数据
                data = cq.load(part)['data']
                url = data['url']
                file_name = data['file']

                # 检查缓存字典
                if file_name in chat_picture_descriptions:
                    description = chat_picture_descriptions[file_name]
                else:
                    # 下载图片并缓存
                    description = chat_client.read_image(url)
                    print(f"read_image: {description}")
                    chat_picture_descriptions[file_name] = description

                lst.append({
                    "type": "text",
                    "text": f"![{description}]({url})"
                })

                # # 读取图片并编码为base64
                # with open(file_path, 'rb') as image_file:
                #     image_tokens = calculate_image_tokens(image_file)
                #     image_bytes = image_file.read()
                # image_base64 = base64.b64encode(image_bytes).decode('utf-8')

                # # 获取文件的MIME类型
                # mime_type = get_mime_type(file_name)

                # lst.append({
                #     "type": "image_url",
                #     "image_url": {
                #         "url": f"data:{mime_type};base64,{image_base64}"
                #     },
                #     "token_cost": image_tokens
                # })
                continue
            except Exception as e:
                # print(f"load picture error: {e}")
                traceback.print_exc()
                # 图片可能读取不到或者下载不了什么的
                lst.append({
                    "type": "text",
                    "text": f"![解析失败的图片]({url})"
                })
        else:
            lst.append({
                "type": "text",
                "text": part
            })

    return lst


def msg2chat(msg, in_group=True):
    if msg.get('sender') and msg['sender']['user_id']==cache.qq:
        role = 'assistant'
        content_pre = []
    else:
        role = 'user'
        if in_group:
            content_pre = [{"type": "text", "text": f'---\nqq: {msg["user_id"]}\nname: {repr(msg2name(msg))}\nmessage_id: {msg["message_id"]}\n---\n'}]
        else:
            content_pre = []
    content = '\n\n'.join([part['text'] for part in content_pre+msg_split(msg['message'])])
    return {'role':role, 'content':content}
    # if msg.get('sender') and msg['sender']['user_id']==cache.qq:
    #     return {'role':'assistant','content':msg['message']}
    # elif in_group:
    #     return {'role':'user','content':msgtext(msg)}
    # else:
    #     return {'role':'user','content':msg['message']}

def chat2msg(chat:dict):
    if chat['role']=='user':
        return chat['content']
    if chat['role']=='assistant':
        return f'[{cache.nicknames[0]}]({cache.qq}): {chat["content"]}'
    raise ValueError(f'错误的role: {chat["role"]}')
###
def get_shownotice():
    return cache.get('shownotice',lambda:False)
###

import datetime
from lunardate import LunarDate
def lunar_time():
    '''获取农历'''
    today = datetime.date.today()
    lunar_date = LunarDate.fromSolarDate(today.year, today.month, today.day)
    return lunar_date

def 小六壬(offset=0):
    tmp = lunar_time()
    # 0 1 2 3 4 ... 21 22 23
    # 0 1 1 2 2 ... 11 11 12
    时辰 = (datetime.datetime.now().hour + 1) // 2
    if 时辰==12:
        # 实际上是为了给日期+1
        时辰 = 1
    return ('大安', '流连', '速喜', '赤口', '小吉', '空亡')[(tmp.month-1 + tmp.day-1 + 时辰) % 6]



def petpet(**kws):
    result = cq.url2cq(f'http://127.0.0.1:2333/petpet?{dict2url(kws)}')
    return result
###
petpet_dic=storage.get('','petpet')
def petpet_trans(s:str):
    if petpet_dic.get(s):
        return petpet_dic[s]
    return s
###
try:
    lst=json.loads(requests.get('http://127.0.0.1:2333/petpet').content.decode())['petData']
except:
    lst = []
petpet_keys=list(map(lambda x:x['key'], lst))
###
def geocode(address):
    paramters = {'address': address, 'output': 'json'}
    base = 'http://api.map.baidu.com/geocoder'
    response = requests.get(base, params=paramters)
    answer = response.json()
    return answer['result']
###
准6 = storage.get('','准6', list)
###

import pprint
###
def fills(s,c,length):
    if not s:return str(c)*length
    s=str(s)
    return str(c)*(length-len(s))+s
def show_mat(mat):
    maxnum=max(max(line) for line in mat)
    n=len(str(maxnum))
    return '\n'.join(' '.join(fills(i,'0',n) for i in line) for line in mat)
###
def move_list(lst):
    n=len(lst)
    lst=list(filter(lambda x:x!=0, lst))
    m=len(lst)
    if m<=1:return lst+[0]*(n-m)
    last=lst[0]
    j=0
    out=[0]*n
    for i in range(1,m):
        if lst[i]==last:
            out[j]=last*2
            last=None
            j+=1
        elif last is None:
            last=lst[i]
        else:
            out[j]=last
            last=lst[i]
            j+=1
    if last is not None:out[j]=last
    return out
###
def rand_if(p):
    return random.random()<=p
def get_empty(mat):
    out=[]
    for i in range(len(mat)):
        n=len(mat[i])
        for j in range(n):
            if mat[i][j]==0:
                out.append((i,j))
    return out
def setp(mat, pos):
    i,j=pos
    mat[i][j]=2 if rand_if(0.75) else 4
def pop_rand(lst):
    return lst.pop(random.randint(0,len(lst)-1))
###
def move_mat(mat, arr):
    if arr=='left':
        return [move_list(line) for line in mat]
    elif arr=='right':
        return [move_list(line[::-1])[::-1] for line in mat]
    elif arr=='up':
        return list(zip(*[move_list(line) for line in zip(*mat)]))
    elif arr=='down':
        return list(zip(*[move_list(line[::-1])[::-1] for line in zip(*mat)]))
    else: return mat
###
def step_2048(mat):
    setp(mat, pop_rand(get_empty(mat)))
###
d2048={
    'w':'up',
    'a':'left',
    's':'down',
    'd':'right',
    '↑':'up',
    '↓':'down',
    '←':'left',
    '→':'right'
}





import ctypes
###
def loadframe(frame):
    ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(frame),ctypes.c_int(0))
###
def vars_update(dic):
    frm = inspect.currentframe().f_back
    frm.f_locals.update(dic)
    ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(frm),ctypes.c_int(0))
###
chat_groups=storage.get('','chat_groups',list)
###
import datetime
###
import time
def get_time():
    '''
    获取当前时间
    '''
    return f'现在是{time.strftime("%Y年%m月%d日%H时%M分%S秒")}'



import os
from io import BytesIO
from PIL import Image
import numpy as np
import matplotlib.font_manager as mfm
from matplotlib import mathtext

def latex2img(text, size=32, color=(0, 0, 0), bg_color=(255, 255, 255), out='demo.png', **kwds):
    """LaTex数学公式转图片
        
        text        - 文本字符串，其中数学公式须包含在两个$符号之间
        size        - 字号，整型，默认32
        color       - 字体颜色，整型三元组，值域范围[0,255]，默认黑色
        bg_color    - 背景颜色，整型三元组，值域范围[0,255]，默认白色
        out         - 文件名，仅支持后缀名为.png的文件名。默认为demo.png，放bot根目录下
        kwds        - 关键字参数
                        dpi         - 输出分辨率（每英寸像素数），默认72
                        family      - 系统支持的字体，None表示当前默认的字体
                        weight      - 笔画轻重，可选项包括：normal（默认）、light和bold
        """
    
    assert os.path.splitext(out)[1].lower() == '.png', '仅支持后缀名为.png的文件名'
    
    for key in kwds:
        if key not in ['dpi', 'family', 'weight']:
            raise KeyError(f'不支持的关键字参数：{key}')
    
    dpi = kwds.get('dpi', 72)
    family = kwds.get('family', None)
    weight = kwds.get('weight', 'normal')
    
    # Set up the Font Properties for rendering
    prop = mfm.FontProperties(family=family, size=size, weight=weight)

    # Create a transparent image to render the formula
    bfo = BytesIO()
    mathtext.math_to_image(text, bfo, prop=prop, dpi=dpi, color=color)

    # Open the image and create an RGBA version with white background
    im = Image.open(bfo).convert("RGBA")
    background = Image.new('RGBA', im.size, bg_color+(255,))
    combined = Image.alpha_composite(background, im)
    
    # Save the final image
    combined.convert("RGB").save(out, 'PNG')
    return f'[CQ:image,file=file://{os.path.abspath(out)}]'
