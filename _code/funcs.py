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
    return send(text, **_msg)

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


# chat_picture_descriptions = storage.get('','chat_picture_descriptions')

# 正则表达式模式
image_pattern = re.compile(r'(\[CQ:image(?:,[^,=]+=[^,\]]*)*\])')
import traceback
# from _code.s3.chat import Chat
# chat_client = Chat()
# 分割消息并处理图片
def msg_split(text):
    lst = []
    for part in image_pattern.split(text):
        if image_pattern.match(part):
            try:
                # 假设cq.load(part)能正确提取图片URL数据
                data = cq.load(part)['data']
                url = data['url']
                # file_name = data['file']

                # # 检查缓存字典
                # if file_name in chat_picture_descriptions:
                #     description = chat_picture_descriptions[file_name]
                # else:
                #     # 下载图片并缓存
                #     description = chat_client.read_image(url)
                #     print(f"read_image: {description}")
                #     chat_picture_descriptions[file_name] = description

                lst.append({
                    "type": "text",
                    # "text": f"![{description}]({url})"
                    "text": f"![]({url})"
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

def msg_split(text):
    lst = []
    for part in image_pattern.split(text):
        if image_pattern.match(part):
            try:
                data = cq.load(part)['data']
                url = data['url']
                # 图片消息使用 image_url 类型
                lst.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url
                    }
                })
            except Exception as e:
                traceback.print_exc()
                lst.append({
                    "type": "text",
                    "text": f"[解析失败的图片]({url})"
                })
        elif part.strip():  # 只添加非空文本
            lst.append({
                "type": "text",
                "text": part
            })
    return lst

def msg2chat(msg, in_group=True):
    if msg.get('sender') and msg['sender']['user_id'] == cache.qq:
        role = 'assistant'
        content = msg_split(msg['message'])  # 返回消息部分列表
    else:
        role = 'user'
        xml_user_id = f'  <user_id>{msg["user_id"]}</user_id>\n'
        xml_name = f'  <name>{repr(msg2name(msg))}</name>\n'
        xml_message_id = f'  <message_id>{msg["message_id"]}</message_id>\n'
        xml_time = f'  <time>{time.strftime("%Y-%m-%d %H:%M",time.localtime(msg["time"]))}</time>\n'
        if in_group:
            header = {
                "type": "text", 
                "text": f'<metadata>\n{xml_user_id}{xml_name}{xml_time}{xml_message_id}</metadata>'
            }
            content = [header] + msg_split(msg['message'])  # 合并header和消息部分
        else:
            header = {
                "type": "text", 
                "text": f'<metadata>\n{xml_time}{xml_message_id}</metadata>'
            }
            content =[header] +  msg_split(msg['message'])  # 合并header和消息部分
            
    # 确保所有内容都是字典格式，而不是字符串
    for item in content:
        if isinstance(item.get('text'), str):
            item['text'] = item['text'].replace('\\', '\\\\').replace('"', '\\"')
    
    return {
        'role': role,
        'content': content  # 直接传递列表，不要转换为字符串
    }

def chat2msg(chat:dict):
    if chat['role']=='user':
        return chat['content']
    if chat['role']=='assistant':
        return f'[{cache.nicknames[0]}]({cache.qq}): {chat["content"]}'
    raise ValueError(f'错误的role: {chat["role"]}')
###
def get_shownotice():
    return cache.get('shownotice',lambda:False) # type: ignore
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
    temp_path = None
    if 'toAvatar' in kws and str(kws['toAvatar']).startswith('http'):
        try:
            import requests, time
            res = requests.get(kws['toAvatar'], timeout=10)
            temp_path = f'/tmp/petpet_{int(time.time())}.png'
            with open(temp_path, 'wb') as f:
                f.write(res.content)
            kws['toAvatar'] = f'file://{temp_path}'
        except:
            pass

    result = cq.url2cq(f'http://127.0.0.1:2334/petpet?{dict2url(kws)}')

    # 用完即焚：自动清理临时文件
    if temp_path:
        try:
            import os
            os.remove(temp_path)
        except:
            pass

    return result
###
petpet_dic=storage.get('','petpet')
def petpet_trans(s:str):
    if petpet_dic.get(s):
        return petpet_dic[s]
    return s
###
def get_petpet_keys():
    try:
        lst=json.loads(requests.get('http://127.0.0.1:2334/petpet', timeout=3).content.decode())['petData']
    except:
        lst = []
    return list(map(lambda x:x['key'], lst))
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
nolog_groups = storage.get('','nolog_groups',list)
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



import select, subprocess

def run_process(args, sendmsg):
    process = subprocess.Popen(args,
                               stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               text=True,
                               bufsize=1)
    # 设置非阻塞模式
    process.stdin.flush()
    process.stdout.flush()
    yield from read_process(process, sendmsg)

def read_process(process, sendmsg):
    while True:
        # 检查是否有输出可读
        readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)


        if process.stdout in readable:
            output = process.stdout.readline()
            if output:
                sendmsg(output.strip())

        if process.stderr in readable:
            error = process.stderr.readline()
            if error:
                sendmsg(f"错误: {error.strip()}")

        if process.poll() is not None:
            break

        # 接收输入并发送给程序
        user_input = yield '等待输入...'
        if not is_msg(user_input):
            sendmsg('请输入文本消息')
            continue
        user_input = user_input['message']
        if process.poll() is not None:
            break
        print(f'输入: {user_input}')
        process.stdin.write(user_input + '\n')
        process.stdin.flush()

    # 读取剩余的输出
    remaining_output, remaining_error = process.communicate()
    if remaining_output:
        sendmsg(remaining_output.strip())
    if remaining_error:
        sendmsg(f"错误: {remaining_error.strip()}")
    sendmsg(f'程序已退出，返回值 {process.returncode}')
    return

import math
from PIL import Image, ImageSequence
def deal_img(pic, size, is_fit):
    pic_url = pic['data']['url']
    pic_path = cq.download_img(pic_url)
    from PIL import Image

    with Image.open(pic_path) as img:
        # 获取缩放比例
        w, h = img.size
        area = w * h
        if isinstance(size, int):
            # fit 且小于等于 则基本不作处理
            # 除非是gif
            if is_fit and area <= size:
                if pic_path.endswith('.gif'):
                    raise ValueError()
                return
            scale = math.sqrt(size / area)
        elif isinstance(size, float):
            scale = size
        else:
            raise Exception('size 必须是整数或浮点数')
        new_size = max(1, int(w * scale)), max(1, int(h * scale))
        # 获取路径
        base, _ = os.path.splitext(pic_path)
        # 如果是 gif 则需要特殊处理
        if pic_path.lower().endswith('.gif'):
            resized_path = f"{base}_resized_{size}.gif"
            # 获取GIF头部信息
            palette = img.getpalette()
            transparency = img.info.get('transparency')
            background = img.info.get('background', 0)
            duration = img.info.get('duration', 100)
            loop = img.info.get('loop', 0)

            resized_frames = []
            frames_duration = []
            frames_disposal = []
            frames_transparency = []

            for frame in ImageSequence.Iterator(img):
                frames_duration.append(frame.info.get('duration', duration))
                frames_disposal.append(frame.disposal_method if hasattr(frame, 'disposal_method') else 2)
                frames_transparency.append(frame.info.get('transparency', transparency))

                # 转RGBA用高质量算法缩放
                frame_rgba = frame.convert('RGBA')
                resized_frame = frame_rgba.resize(new_size, Image.LANCZOS)

                # 再转换回调色板模式 ("P")，以回收调色板模式优势
                resized_frame_p = resized_frame.convert('P', palette=Image.ADAPTIVE, colors=256)
                resized_frames.append(resized_frame_p)

            # 准备保存的额外参数
            save_kwargs = {
                'save_all': True,
                'append_images': resized_frames[1:],
                'duration': frames_duration,
                'loop': loop,
                'background': background,
                'optimize': False,
                'disposal': frames_disposal,
            }
            # 如果存在透明色，添加透明索引
            if transparency is not None:
                save_kwargs['transparency'] = transparency
            
            # 保存GIF动画
            resized_frames[0].save(resized_path, **save_kwargs)
        else:
            resized_path = f"{base}_resized_{size}.png"
            # 如果是一般图片
            # 图片模式转换
            if img.mode not in ('RGBA', 'RGB'):
                img = img.convert('RGBA' if img.mode == 'P' else 'RGB')

            # 保存调整后的图片
            img.resize(new_size, Image.Resampling.LANCZOS).save(resized_path, format="PNG")
        
        return os.path.abspath(resized_path)
 
from typing import Dict, List, Optional
import time
import jwt
import requests
import json
from pathlib import Path

API_HOST = "ma4bj98cvh.re.qweatherapi.com"
KEY_ID = "CNPKE8B4G8"
PROJECT_ID = "2G877M3PJY"
TOKEN_CACHE = Path.home() / ".qweather-token"

# 内部工具函数
def _load_private_key() -> str:
    """加载Ed25519私钥"""
    try:
        with open("ed25519-private.pem", "r") as f:
            return f.read()
    except Exception as e:
        raise RuntimeError(f"无法读取私钥文件: {e}")

def _generate_token() -> str:
    """生成JWT认证令牌"""
    private_key = _load_private_key()
    now = int(time.time())
    payload = {'iat': now-30, 'exp': now+900, 'sub': PROJECT_ID}
    return jwt.encode(payload, private_key, algorithm='EdDSA', headers={'kid': KEY_ID})

def _get_cached_token() -> Optional[str]:
    """获取缓存的JWT令牌"""
    try:
        if TOKEN_CACHE.exists():
            with open(TOKEN_CACHE, "r") as f:
                data = json.load(f)
                if data['exp'] > time.time():
                    return data['token']
    except Exception:
        pass
    return None

def _api_request(endpoint: str, params: Dict) -> Optional[Dict]:
    """执行API请求"""
    token = _get_cached_token() or _generate_token()
    url = f"https://{API_HOST}{endpoint}"
    
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None

# 公开API函数
def search_city(location: str, adm: str = "", lang: str = "zh") -> Optional[List[Dict]]:
    '''
    城市地理位置搜索
    返回包含城市信息的字典列表，搜索失败返回None

    @param
    location: 需要查询地区的名称，支持文字、以英文逗号分隔的'经度,纬度'（十进制，最多支持小数点后两位）、LocationID或Adcode(仅限中国城市)
    adm: 上级行政区划用于过滤结果
    lang: 返回语言，默认中文
    '''
    params = {"location": location, "adm": adm, "lang": lang}
    response = _api_request("/geo/v2/city/lookup", params)
    return response.get('location') if response and response.get('code') == "200" else None

def get_realtime_weather(location_id: str, unit: str = "m") -> Optional[Dict]:
    '''
    获取实时天气数据
    返回字典包含字段：温度(temp/℃)、体感温度(feelsLike/℃)、天气状况(text)、
    风向(windDir)、风力等级(windScale)、风速(windSpeed/km/h)、湿度(humidity/%)、
    观测时间(obsTime/ISO8601)、能见度(vis/km)、气压(pressure/hPa)、降水量(precip/mm)
    查询失败返回None

    @param
    location_id: 通过search_city获取的位置ID
    unit: 单位制，m-公制/i-英制
    '''
    response = _api_request("/v7/weather/now", {"location": location_id, "unit": unit})
    return response.get('now') if response and response.get('code') == "200" else None

def get_daily_forecast(location_id: str, days: int = 3, unit: str = "m") -> Optional[List[Dict]]:
    '''
    获取多日天气预报
    返回字典列表，每个字典包含字段：预报日期(fxDate/YYYY-MM-DD)、最高温度(tempMax/℃)、
    最低温度(tempMin/℃)、白天天气(textDay)、夜间天气(textNight)、
    日出时间(sunrise/HH:MM)、日落时间(sunset/HH:MM)、风向(windDirDay)、
    风力等级(windScale)、风速(windSpeed/km/h)

    @param
    location_id: 地理位置ID
    days: 预报天数(3|7|10|15|30)，默认3天
    unit: 单位制，m-公制/i-英制
    '''
    valid_days = {3: '3d', 7: '7d', 10: '10d', 15: '15d', 30: '30d'}
    endpoint = f"/v7/weather/{valid_days.get(days, '3d')}"
    response = _api_request(endpoint, {"location": location_id, "unit": unit})
    return response.get('daily') if response and response.get('code') == "200" else None

def get_hourly_forecast(location_id: str, hours: int = 24, unit: str = "m") -> Optional[List[Dict]]:
    '''
    获取逐小时天气预报
    返回字典列表，每个字典包含字段：预报时间(fxTime/ISO8601)、温度(temp/℃)、
    天气状况(text)、风向(windDir)、风力等级(windScale)、风速(windSpeed/km/h)、
    降水量(precip/mm)、降水概率(pop/%)、湿度(humidity/%)、气压(pressure/hPa)

    @param
    location_id: 地理位置ID
    hours: 预报小时数(24|72|168)，默认24小时
    unit: 单位制，m-公制/i-英制
    '''
    valid_hours = {24: '24h', 72: '72h', 168: '168h'}
    endpoint = f"/v7/weather/{valid_hours.get(hours, '24h')}"
    response = _api_request(endpoint, {"location": location_id, "unit": unit})
    return response.get('hourly') if response and response.get('code') == "200" else None


import tiktoken

encoding = tiktoken.encoding_for_model('gpt-4')

def count_tokens(text:str):
    return len(encoding.encode(text))

道德经=['''【第一章】道可道，非常道；名可名，非常名。无名天地之始，有名万物之母。故常无欲，以观其妙；常有欲，以观其徼（jiào）。此两者同出而异名，同谓之玄，玄之又玄，众妙之门。''','''【第二章】天下皆知美之为美，斯恶（è）已；皆知善之为善，斯不善已。故有无相生，难易相成，长短相较，高下相倾，音声相和（hè），前后相随。是以圣人处无为之事，行不言之教，万物作焉而不辞，生而不有，为而不恃，功成而弗居。夫（fú）唯弗居，是以不去。 ''','''【第三章】不尚贤，使民不争；不贵难得之货，使民不为盗；不见（xiàn）可欲，使民心不乱。是以圣人之治，虚其心，实其腹；弱其志，强其骨。常使民无知无欲，使夫（fú）智者不敢为也。为无为，则无不治。 ''','''【第四章】道冲而用之或不盈，渊兮似万物之宗。挫其锐，解其纷，和其光，同其尘。湛兮似或存，吾不知谁之子，象帝之先。''','''【第五章】天地不仁，以万物为刍（chú）狗；圣人不仁，以百姓为刍狗。天地之间，其犹橐龠（tuó yuè）乎？虚而不屈，动而愈出。多言数（shuò）穷，不如守中。 ''','''【第六章】谷神不死，是谓玄牝（pìn），玄牝之门，是谓天地根。绵绵若存，用之不勤。 ''','''【第七章】天长地久。天地所以能长且久者，以其不自生，故能长生。是以圣人后其身而身先，外其身而身存。非以其无私邪（yé）？故能成其私。''','''【第八章】上善若水。水善利万物而不争，处众人之所恶（wù），故几（jī）于道。居善地，心善渊，与善仁，言善信，正善治，事善能，动善时。夫唯不争，故无尤。 ''','''【第九章】持而盈之，不如其已。揣(chuǎi)而锐之，不可长保。金玉满堂，莫之能守。富贵而骄，自遗（yí）其咎。功成身退，天之道。 ''','''【第十章】载（zài）营魄抱一，能无离乎？专气致柔，能婴儿乎？涤除玄览，能无疵乎？爱民治国，能无知（zhì）乎？天门开阖（hé），能无雌乎？明白四达，能无为乎？生之、畜（xù）之，生而不有，为而不恃，长（zhǎng）而不宰，是谓玄德。 ''','''【第十一章】三十辐共一毂（gǔ），当其无，有车之用。埏埴（shān zhí）以为器，当其无，有器之用。凿户牖（yǒu）以为室，当其无，有室之用。故有之以为利，无之以为用。 ''','''【第十二章】 五色令人目盲，五音令人耳聋，五味令人口爽，驰骋畋（tián）猎令人心发狂，难得之货令人行妨。是以圣人为腹不为目，故去彼取此。''','''【第十三章】宠辱若惊，贵大患若身。何谓宠辱若惊？宠为下，得之若惊，失之若惊，是谓宠辱若惊。何谓贵大患若身？吾所以有大患者，为吾有身，及吾无身，吾有何患！故贵以身为天下，若可寄天下；爱以身为天下，若可托天下。 ''','''【第十四章】视之不见名曰夷，听之不闻名曰希，搏之不得名曰微。此三者不可致诘（jié），故混（hùn）而为一。其上不皦（jiǎo皎），其下不昧。绳绳(mǐn mǐn )不可名，复归于无物，是谓无状之状，无物之象。是谓惚恍。迎之不见其首，随之不见其后。执古之道，以御今之有，能知古始，是谓道纪。''','''【第十五章】古之善为士者，微妙玄通，深不可识。夫唯不可识，故强(qiǎng)为之容。豫焉若冬涉川，犹兮若畏四邻，俨兮其若容，涣兮若冰之将释，敦兮其若朴，旷兮其若谷，混兮其若浊。孰能浊以静之徐清？孰能安以久动之徐生？保此道者不欲盈，夫唯不盈，故能蔽不新成。 ''','''【第十六章】致虚极，守静笃（dǔ），万物并作，吾以观复。夫物芸芸，各复归其根。归根曰静，是谓复命。复命曰常，知常曰明，不知常，妄作，凶。知常容，容乃公，公乃王（wàng），王（wàng）乃天，天乃道，道乃久，没（mò）身不殆。 ''','''【第十七章】太上，下知有之。其次，亲而誉之。其次，畏之。其次，侮之。信不足焉，有不信焉。悠兮其贵言。功成事遂，百姓皆谓我自然。 ''','''【第十八章】大道废，有仁义；慧智出，有大伪；六亲不和，有孝慈；国家昏乱，有忠臣。''','''【第十九章】绝圣弃智，民利百倍；绝仁弃义，民复孝慈；绝巧弃利，盗贼无有。此三者，以为文不足，故令有所属，见（xiàn）素抱朴，少私寡欲。 ''','''【第二十章】绝学无忧。唯之与阿（ē），相去几何？善之与恶，相去若何？人之所畏，不可不畏。荒兮其未央哉！众人熙熙，如享太牢，如春登台。我独泊兮其未兆，如婴儿之未孩。傫傫（lěi）兮若无所归。众人皆有余，而我独若遗。我愚人之心也哉！沌沌兮！俗人昭昭，我独昏昏；俗人察察，我独闷闷。澹（dàn）兮其若海，飂（liù）兮若无止。众人皆有以，而我独顽似鄙。我独异于人，而贵食(sì)母。 ''','''【第二十一章】孔德之容，惟道是从。道之为物，惟恍惟惚。惚兮恍兮，其中有象；恍兮惚兮，其中有物。窈（yǎo）兮冥兮，其中有精；其精甚真，其中有信。自古及今，其名不去，以阅众甫。吾何以知众甫之状哉？以此。 ''','''【第二十二章】曲则全，枉则直，洼则盈，敝则新，少则得，多则惑。是以圣人抱一，为天下式。不自见（xiàn）故明，不自是故彰，不自伐故有功，不自矜故长。夫唯不争，故天下莫能与之争。古之所谓曲则全者，岂虚言哉！诚全而归之。 ''','''【第二十三章】希言自然。故飘风不终朝（zhāo），骤雨不终日。孰为此者？天地。天地尚不能久，而况于人乎？故从事于道者，道者同于道，德者同于德，失者同于失。同于道者，道亦乐得之；同于德者，德亦乐得之；同于失者，失亦乐得之。信不足焉，有不信焉。 ''','''【第二十四章】企者不立，跨者不行，自见（xiàn）者不明，自是者不彰，自伐者无功，自矜者不长。其在道也，曰余食赘（zhuì）行。物或恶（wù）之，故有道者不处（chǔ）。 ''','''【第二十五章】有物混（hùn）成，先天地生。寂兮寥兮，独立不改，周行而不殆，可以为天下母。吾不知其名，字之曰道，强(qiǎng)为之名曰大。大曰逝，逝曰远，远曰反。故道大，天大，地大，王亦大。域中有四大，而王居其一焉。人法地，地法天，天法道，道法自然。 ''','''【第二十六章】重为轻根，静为躁君。是以圣人终日行不离辎（zī）重。虽有荣观（guàn），燕处超然，奈何万乘（shèng）之主，而以身轻天下？轻则失本，躁则失君。 ''','''【第二十七章】善行无辙迹，善言无瑕谪(xiá zhé)，善数（shǔ）不用筹策，善闭无关楗（jiàn）而不可开，善结无绳约而不可解。是以圣人常善救人，故无弃人；常善救物，故无弃物，是谓袭明。故善人者，不善人之师；不善人者，善人之资。不贵其师，不爱其资，虽智大迷，是谓要妙。 ''','''【第二十八章】知其雄，守其雌，为天下溪。为天下溪，常德不离，复归于婴儿。知其白，守其黑，为天下式。为天下式，常德不忒（tè），复归于无极。知其荣，守其辱，为天下谷。为天下谷，常德乃足，复归于朴。朴散则为器，圣人用之则为官长（zhǎng）。故大制不割。 ''','''【第二十九章】将欲取天下而为之，吾见其不得已。天下神器，不可为也。为者败之，执者失之。故物或行或随，或歔（xū）或吹，或强或羸（léi），或挫或隳（huī）。是以圣人去甚，去奢，去泰。 ''','''【第三十章】以道佐人主者，不以兵强天下，其事好（hào）还。师之所处，荆棘生焉。大军之后，必有凶年。善有果而已，不敢以取强。果而勿矜，果而勿伐，果而勿骄，果而不得已，果而勿强。物壮则老，是谓不道，不道早已。 ''','''【第三十一章】夫佳兵者，不祥之器。物或恶（wù）之，故有道者不处（chǔ）。君子居则贵左，用兵则贵右。兵者，不祥之器，非君子之器。不得已而用之，恬淡为上，胜而不美。而美之者，是乐(yào)杀人。夫乐(yào)杀人者，则不可以得志于天下矣。吉事尚左，凶事尚右。偏将军居左，上将军居右，言以丧（sāng）礼处之。杀人之众，以哀悲泣之，战胜，以丧礼处之。 ''','''【第三十二章】道常无名，朴虽小，天下莫能臣也。侯王若能守之，万物将自宾。天地相合以降甘露，民莫之令而自均。始制有名，名亦既有，夫亦将知止。知止可以不殆。譬道之在天下，犹川谷之于江海。''','''【第三十三章】知人者智，自知者明。胜人者有力，自胜者强。知足者富，强行者有志，不失其所者久，死而不亡者寿。 ''','''【第三十四章】大道泛兮，其可左右。万物恃之而生而不辞，功成不名有，衣养万物而不为主，常无欲，可名于小；万物归焉而不为主，可名为大。以其终不自为大，故能成其大。 ''','''【第三十五章】执大象，天下往；往而不害，安平太。乐（yuè）与饵，过客止。道之出口，淡乎其无味，视之不足见（jiàn），听之不足闻，用之不足既。 ''','''【第三十六章】将欲歙（xī）之，必固张之；将欲弱之，必固强之；将欲废之，必固兴之；将欲夺之，必固与之，是谓微明。柔弱胜刚强。鱼不可脱于渊，国之利器不可以示人。 ''','''【第三十七章】道常无为而无不为，侯王若能守之，万物将自化。化而欲作，吾将镇之以无名之朴。无名之朴，夫亦将无欲。不欲以静，天下将自定。 ''','''【第三十八章】上德不德，是以有德；下德不失德，是以无德。上德无为而无以为，下德为之而有以为。上仁为之而无以为，上义为之而有以为，上礼为之而莫之应，则攘(rǎng)臂而扔之。故失道而后德，失德而后仁，失仁而后义，失义而后礼。夫礼者，忠信之薄(bó)而乱之首。前识者，道之华而愚之始。是以大丈夫处其厚，不居其薄(bó)；处其实，不居其华。故去彼取此。 ''','''【第三十九章】昔之得一者，天得一以清，地得一以宁，神得一以灵，谷得一以盈，万物得一以生，侯王得一以为天下贞。其致之。天无以清将恐裂，地无以宁将恐发（fèi，“发”通“废”），神无以灵将恐歇，谷无以盈将恐竭，万物无以生将恐灭，侯王无以贵高将恐蹶（jué）。故贵以贱为本，高以下为基。是以侯王自谓孤寡不穀（谷gǔ）。此非以贱为本邪（yé）？非乎？故致数（shuò）舆（yù）无舆。不欲琭（lù）琭如玉，珞（luò）珞如石。 ''','''【第四十章】反者，道之动；弱者，道之用。天下万物生于有，有生于无。''']
###
道德经.extend(['''【第四十一章】 上士闻道，勤而行之；中士闻道，若存若亡；下士闻道，大笑之，不笑不足以为道。故建言有之：明道若昧，进道若退，夷道若颣（lèi）。上德若谷，大白若辱，广德若不足，建德若偷，质真若渝（yú）。大方无隅（yú），大器晚成，大音希声，大象无形。道隐无名，夫唯道善贷且成。 ''','''【第四十二章】道生一，一生二，二生三，三生万物。万物负阴而抱阳，冲气以为和。人之所恶（wù），唯孤寡不穀（谷gǔ），而王公以为称（chēng）。故物，或损之而益，或益之而损。人之所教（jiào），我亦教之。强梁者不得其死，吾将以为教父。 ''','''【第四十三章】天下之至柔，驰骋天下之至坚，无有入无间，吾是以知无为之有益。不言之教，无为之益，天下希及之。 ''','''【第四十四章】名与身孰亲？身与货孰多？得与亡孰病？ 是故甚爱必大费，多藏必厚亡。知足不辱，知止不殆，可以长久。 ''','''【第四十五章】大成若缺，其用不弊。大盈若冲，其用不穷。大直若屈，大巧若拙，大辩若讷。躁胜寒，静胜热。清静为天下正。 ''','''【第四十六章】天下有道，却走马以粪；天下无道，戎马生于郊。祸莫大于不知足，咎莫大于欲得，故知足之足，常足矣。''','''【第四十七章】不出户，知天下；不窥牖，见天道。其出弥远，其知弥少。是以圣人不行而知，不见而名，不为而成。 ''','''【第四十八章】为学日益，为道日损。损之又损，以至于无为，无为而无不为。取天下常以无事，及其有事，不足以取天下。 ''','''【第四十九章】圣人无常心，以百姓心为心。善者，吾善之；不善者，吾亦善之，德善。信者，吾信之；不信者，吾亦信之，德信。圣人在天下歙歙（xīxī），为天下浑其心。（百姓皆注其耳目），圣人皆孩之。 ''','''【第五十章】出生入死。生之徒十有三，死之徒十有三。人之生动之死地，亦十有三。夫何故？以其生生之厚。盖闻善摄生者，陆行不遇兕（sì）虎，入军不被（pī）甲兵，兕无所投其角，虎无所措其爪（zhǎo），兵无所容其刃。夫何故？以其无死地。 ''','''【第五十一章】道生之，德畜（xù）之，物形之，势成之。是以万物莫不尊道而贵德。道之尊，德之贵，夫莫之命而常自然。故道生之，德畜之。长之、育之、亭之、毒之、养之、覆之。生而不有，为而不恃，长（zhǎng）而不宰，是谓玄德。 ''','''【第五十二章】天下有始，以为天下母。既得其母，以知其子；既知其子，复守其母，没（mò）身不殆。塞（sè）其兑，闭其门，终身不勤。开其兑，济其事，终身不救。见（jiàn）小曰明，守柔曰强。用其光，复归其明，无遗身殃，是为习常。 ''','''【第五十三章】使我介然有知，行于大道，唯施（迤yí）是畏。大道甚夷，而民好径。朝（cháo）甚除，田甚芜，仓甚虚。服文彩，带利剑，厌饮食，财货有余，是为盗夸。非道也哉！ ''','''【第五十四章】善建者不拔，善抱者不脱，子孙以祭祀不辍。修之于身，其德乃真；修之于家，其德乃余；修之于乡，其德乃长（zhǎng）；修之于国，其德乃丰；修之于天下，其德乃普。故以身观身，以家观家，以乡观乡，以国观国，以天下观天下。吾何以知天下然哉？以此。 ''','''【第五十五章】 含德之厚，比于赤子。蜂虿（chài）虺（huǐ）蛇不螫(shì)，猛兽不据，攫(jué)鸟不搏。骨弱筋柔而握固。未知牝牡之合而全作，精之至也。终日号而不嗄（shà），和之至也。知和曰常，知常曰明，益生曰祥，心使气曰强。物壮则老，谓之不道，不道早已。 ''','''【第五十六章】知（zhì）者不言，言者不知（zhì）。塞（sè）其兑，闭其门，挫其锐；解其纷，和其光，同其尘，是谓玄同。故不可得而亲，不可得而疏；不可得而利，不可得而害；不可得而贵，不可得而贱，故为天下贵。''','''【第五十七章】以正治国，以奇用兵，以无事取天下。吾何以知其然哉？以此。天下多忌讳，而民弥贫；民多利器，国家滋昏；人多伎（jì）巧，奇物滋起；法令滋彰，盗贼多有。故圣人云：“我无为而民自化，我好静而民自正，我无事而民自富，我无欲而民自朴。” ''','''【第五十八章】其政闷闷，其民淳淳；其政察察，其民缺缺。祸兮福之所倚，福兮祸之所伏。孰知其极？其无正。正复为奇，善复为妖，人之迷，其日固久。是以圣人方而不割，廉而不刿（guì），直而不肆，光而不耀。''','''【第五十九章】治人事天莫若啬（sè）。夫唯啬，是谓早服。早服谓之重(chóng)积德，重(chóng)积德则无不克，无不克则莫知其极，莫知其极，可以有国。有国之母，可以长久。是谓深根固柢（dǐ），长生久视之道。 ''','''【第六十章】治大国若烹小鲜。以道莅（lì）天下，其鬼不神。非其鬼不神，其神不伤人；非其神不伤人，圣人亦不伤人。夫两不相伤，故德交归焉。''','''【第六十一章】大国者下流。天下之交，天下之牝。牝常以静胜牡，以静为下。故大国以下小国，则取小国；小国以下大国，则取大国。故或下以取，或下而取。大国不过欲兼畜（xù）人，小国不过欲入事人，夫两者各得其所欲，大者宜为下。 ''','''【第六十二章】道者万物之奥，善人之宝，不善人之所保。美言可以市，尊行可以加人。人之不善，何弃之有！故立天子，置三公，虽有拱璧以先驷马，不如坐进此道。古之所以贵此道者何？不曰以求得，有罪以免邪（yé）？故为天下贵。''','''【第六十三章】为无为，事无事，味无味。大小多少，报怨以德。图难于其易，为大于其细。天下难事必作于易，天下大事必作于细，是以圣人终不为大，故能成其大。夫轻诺必寡信，多易必多难，是以圣人犹难之。故终无难矣。 ''','''【第六十四章】其安易持，其未兆易谋，其脆易泮（pàn），其微易散。为之于未有，治之于未乱。合抱之木，生于毫末；九层之台，起于累土；千里之行，始于足下。为者败之，执者失之。是以圣人无为，故无败；无执，故无失。民之从事，常于几成而败之。慎终如始，则无败事。是以圣人欲不欲，不贵难得之货。学不学，复众人之所过。以辅万物之自然，而不敢为。 ''','''【第六十五章】古之善为道者，非以明民，将以愚之。民之难治，以其智多。故以智治国，国之贼；不以智治国，国之福。知此两者，亦稽（jī）式。常知稽式，是谓玄德。玄德深矣，远矣，与物反矣，然后乃至大顺。 ''','''【第六十六章】江海所以能为百谷王者，以其善下之，故能为百谷王。是以欲上民，必以言下之；欲先民，必以身后之。是以圣人处上而民不重，处前而民不害，是以天下乐推而不厌。以其不争，故天下莫能与之争。 ''','''【第六十七章】天下皆谓我道大，似不肖（xiào）。夫唯大，故似不肖。若肖，久矣其细也夫。我有三宝，持而保之。一曰慈，二曰俭，三曰不敢为天下先。慈，故能勇；俭，故能广；不敢为天下先，故能成器长（zhǎng）。今舍慈且勇，舍俭且广，舍后且先，死矣！夫慈，以战则胜，以守则固，天将救之，以慈卫之。 ''','''【第六十八章】善为士者不武，善战者不怒，善胜敌者不与，善用人者为之下。是谓不争之德，是谓用人之力，是谓配天古之极。 ''','''【第六十九章】用兵有言，吾不敢为主而为客，不敢进寸而退尺。是谓行（xíng）无行（háng），攘(rǎng)无臂，扔无敌，执无兵。祸莫大于轻敌，轻敌几丧吾宝。故抗兵相加，哀者胜矣。 ''','''【第七十章】吾言甚易知，甚易行，天下莫能知，莫能行。言有宗，事有君。夫唯无知，是以不我知。知我者希，则我者贵，是以圣人被（pī，“被”同“披”）褐怀玉。 ''','''【第七十一章】知不知，上；不知知，病。夫唯病病，是以不病。圣人不病，以其病病，是以不病。''','''【第七十二章】民不畏威，则大威至。无狎（xiá）其所居，无厌（yà，“厌”同“压”）其所生。夫唯不厌（yà，“厌”同“压”），是以不厌(yàn)。是以圣人自知，不自见（xiàn）；自爱，不自贵。故去彼取此。''','''【第七十三章】勇于敢则杀，勇于不敢则活。此两者，或利或害。天之所恶（wù），孰知其故？是以圣人犹难之。天之道，不争而善胜，不言而善应，不召而自来，繟（chǎn）然而善谋。天网恢恢，疏而不失。 ''','''【第七十四章】民不畏死，奈何以死惧之！若使民常畏死，而为奇者，吾得执而杀之，孰敢？常有司杀者杀，夫代司杀者杀，是谓代大匠斫（zhuó）。夫代大匠斫者，希有不伤其手矣。 ''','''【第七十五章】民之饥，以其上食税之多，是以饥。民之难治，以其上之有为，是以难治。民之轻死，以其求生之厚，是以轻死。夫唯无以生为者，是贤于贵生。 ''','''【第七十六章】人之生也柔弱，其死也坚强。万物草木之生也柔脆，其死也枯槁。故坚强者死之徒，柔弱者生之徒。是以兵强则不胜，木强则兵。强大处下，柔弱处上。''','''【第七十七章】天之道，其犹张弓与！高者抑之，下者举之；有余者损之，不足者补之。天之道，损有余而补不足。人之道则不然，损不足以奉有余。孰能有余以奉天下？唯有道者。是以圣人为而不恃，功成而不处，其不欲见（xiàn）贤。 ''','''【第七十八章】天下莫柔弱于水，而攻坚强者莫之能胜，其无以易之。弱之胜强，柔之胜刚，天下莫不知，莫能行。是以圣人云，受国之垢，是谓社稷主；受国不祥，是为天下王。正言若反。 ''','''【第七十九章】和大怨，必有余怨，安可以为善？是以圣人执左契，而不责于人。有德司契，无德司彻。天道无亲，常与善人。''','''【第八十章】小国寡民，使有什伯（bǎi）之器而不用，使民重（zhòng）死而不远徙(xí)。虽有舟舆，无所乘之；虽有甲兵，无所陈之；使人复结绳而用之。甘其食，美其服，安其居，乐其俗。邻国相望，鸡犬之声相闻，民至老死不相往来。''','''【第八十一章】信言不美，美言不信；善者不辩，辩者不善；知（zhì）者不博，博者不知（zhì）。圣人不积，既以为人，己愈有；既以与人，己愈多。天之道，利而不害。圣人之道，为而不争。'''])

