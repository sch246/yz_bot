'''延时任务'''
import re
import time
from typing import Callable
from itertools import count
from threading import Lock, Timer
import traceback
import inspect

from main import cache, storage, cache_args

from main import read_params, cmds

from bot.cmds import py
pyrun = py.run

def later(sec:float, action:Callable, argument:tuple=(), kwargs:dict={})->int:
    timer = Timer(sec, action, argument, kwargs)
    timer.daemon = True
    timer.start()
    return timer.cancel
def at(sec:float, action:Callable, argument:tuple=(), kwargs:dict={})->int:
    return later(sec-time.time(), action, argument, kwargs)


storage.storage.setdefault('later_list/groups',{})
storage.storage.setdefault('later_list/users',{})
_id_iter = count()

def get_later_list(msg:dict = None)->list[dict]:
    if msg is None:
        msg = cache.thismsg()
    if 'group_id' in msg:
        return storage.get('later_list/groups', str(msg['group_id']), list)
    else:
        return storage.get('later_list/users', str(msg['user_id']), list)

def _list_pop(seq:int, msg:dict):
    """按照seq弹出later_list的元素
    """
    later_list = get_later_list(msg)
    with get_lock(*msg_id(msg)):
        for i, elem in enumerate(later_list.copy()):
            if elem['seq'] == seq:
                del later_list[i]
                return elem
    return {}

def _action(seq, expr, msg):
    _list_pop(seq, msg)
    pyrun(expr, msg, skip_op=True, insert={'later_repeat':repeat})

def repeat(reltime:str):
    '''在触发的任务中使用，用于重复执行任务'''
    loc = inspect.currentframe().f_back.f_locals
    expr, msg = loc.get('_py_expr'), loc.get('_py_msg')
    enter(read_reltime(reltime), expr, msg)

def msg_id(msg:dict):
    if 'group_id' in msg:
        return True, msg['group_id']
    else:
        return False, msg['user_id']

@cache_args
def get_lock(is_group, id):
    return Lock()

def _read_YMD_hms(s_time:str):
    return time.mktime(time.strptime(s_time, '%Y-%m-%d %H:%M:%S'))

def process_later_list(is_group, later_list):
    id_key = 'group_id' if is_group else 'user_id'
    with get_lock(is_group, id_key):
        for later in later_list:
            try:
                YMD_hms, expr, msg = later.get('YMD_hms'), later.get('expr'), later.get('msg')
                seq = next(_id_iter)
                later['seq'] = seq
                later['cancel'] = at(_read_YMD_hms(YMD_hms), _action, (seq, expr, msg))
            except:
                traceback.print_exc()

for group_id, later_list in storage.storage['later_list/groups'].items():
    process_later_list(True, later_list)

for user_id, later_list in storage.storage['later_list/users'].items():
    process_later_list(False, later_list)


def _get_days_in_month(year, month):
    days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[2] = 29  # 处理闰年
    return days_in_month[month]

def normalize_time_tuple(*time_tuple):
    '''不处理负数'''
    year, month, day, hour, minute, second, *last = time_tuple

    # 处理秒
    minute += second // 60
    second = second % 60

    # 处理分
    hour += minute // 60
    minute = minute % 60

    # 处理时
    day += hour // 24
    hour = hour % 24

    # 处理月
    year += (month-1) // 12
    month = (month-1) % 12 +1

    # 处理日
    while day > (days:=_get_days_in_month(year, month)):
        day -= days
        month += 1
        if month > 12:
            year += 1
            month = 1

    # 返回修正后的时间元组
    return year, month, day, hour, minute, second, *last

re_m = re.compile(r'^\d+-\d+(-\d+)?$')
re_M = re.compile(r'^\d+:\d+(:\d+)?$')
re_abstime = re.compile(r'^\d+:\d+(:\d+)?|\d+-\d+(-\d+)?( \d+:\d+(:\d+)?)?$')
def has_time_passed(hour, minute, second):
    # 获取当前时间的struct_time对象
    now = time.localtime()
    
    # 创建一个struct_time对象表示今天的给定时分秒
    target_time = time.struct_time((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, second, 
                                    now.tm_wday, now.tm_yday, now.tm_isdst))
    
    # 将struct_time对象转换为时间戳
    target_timestamp = time.mktime(target_time)
    
    # 获取当前时间的时间戳
    current_timestamp = time.mktime(now)
    
    # 比较当前时间与给定时间
    return current_timestamp > target_timestamp
def read_abstime(s_time:str):
    '''
    完整格式是 年-月-日 时:分:秒
    但是可以忽略年月日或者时分秒
    年月日也可以写成月日
    时分秒也可以写成时分
    格式错误会报错:SyntaxError
    注意，当忽略时分秒时，默认的时分秒是4:0:0而不是0:0:0
    错误的日期将会报错
    '''
    t = s_time.split()
    if len(t) not in [1,2]: raise ValueError(f'绝对时间字符串格式错误，得到了: {repr(s_time)}')
    if len(t)==1:
        t = t[0]
        if re_m.match(t):
            年月日 = list(map(int,t.split('-')))
            if len(年月日)==2:
                年月日.insert(0, time.localtime().tm_year)
            年月日时分秒 = 年月日+[4,0,0]
        elif re_M.match(t):
            时分秒 = list(map(int,t.split(':')))
            if len(时分秒)==2:
                时分秒.append(0)
            if has_time_passed(*时分秒):
                (Y,m,d,_,_,_,_,_,_)  = time.localtime(time.time()+86400)
            else:
                (Y,m,d,_,_,_,_,_,_)  = time.localtime()
            年月日时分秒 = [Y,m,d]+时分秒
        else:
            raise ValueError(f'绝对时间字符串格式错误，得到了: {repr(s_time)}')
    else:
        m, M = t
        if not re_m.match(m): raise ValueError(f'绝对时间字符串格式错误，得到了: {repr(s_time)}')
        if not re_M.match(M): raise ValueError(f'绝对时间字符串格式错误，得到了: {repr(s_time)}')
        年月日 = list(map(int,m.split('-')))
        时分秒 = list(map(int,M.split(':')))
        if len(年月日)==2:
            年月日.insert(0, time.localtime().tm_year)
        if len(时分秒)==2:
            时分秒.append(0)
        年月日时分秒 = 年月日 + 时分秒
    return time.strftime('%Y-%m-%d %H:%M:%S', (*年月日时分秒, 0,0,0))

re_reltime = re.compile(r'^(\d+(?:Y|y))?(\d+M)?(\d+(?:D|d))?(\d+h)?(\d+m)?(\d+s)?$')
def read_reltime(s_time:str):
    # 匹配时间字符串
    match = re_reltime.match(s_time)
    if not match:
        raise ValueError(f'绝对时间字符串格式错误，得到了: {repr(s_time)}')

    # 解析时间字符串中的年、月、日、时、分、秒信息
    y = int(match.group(1)[:-1]) if match.group(1) else 0
    M = int(match.group(2)[:-1]) if match.group(2) else 0
    d = int(match.group(3)[:-1]) if match.group(3) else 0
    h = int(match.group(4)[:-1]) if match.group(4) else 0
    m = int(match.group(5)[:-1]) if match.group(5) else 0
    s = int(match.group(6)[:-1]) if match.group(6) else 0

    # 获取当前本地时间
    t = time.localtime()

    # 计算相对时间
    return time.strftime('%Y-%m-%d %H:%M:%S', normalize_time_tuple(t.tm_year+y, t.tm_mon+M, t.tm_mday+d, t.tm_hour+h, t.tm_min+m, t.tm_sec+s, 0, 0, t.tm_isdst))

re_time = re.compile(r'^\d+:\d+(:\d+)?|\d+-\d+(-\d+)?( \d+:\d+(:\d+)?)?|(\d+(Y|y))?(\d+M)?(\d+(D|d))?(\d+h)?(\d+m)?(\d+s)?')
def enter(s_time:str, expr:str, msg:dict):
    if re_reltime.match(s_time):
        YMD_hms = read_reltime(s_time)
    elif re_abstime.match(s_time):
        YMD_hms = read_abstime(s_time)
    else:
        raise ValueError(f'enter的时间格式错误: {repr(s_time)}')
    later_list = get_later_list(msg)
    seq = next(_id_iter)
    later_list.insert(0, {
        'YMD_hms':YMD_hms,
        'expr':expr,
        'msg':msg,
        'seq':seq,
        'cancel':at(_read_YMD_hms(YMD_hms), _action, (seq, expr, msg)),
    })
    return seq, YMD_hms

def cancel(seq, msg):
    '''根据seq取消任务'''
    elem = _list_pop(seq, msg)
    if elem:
        elem['cancel']()
        return elem.get('YMD_hms'), elem.get('expr')
    return '删除失败', '任务不存在或不在当前聊天区域'

def change(seq, s_time, expr, msg):
    if re_reltime.match(s_time):
        YMD_hms = read_reltime(s_time)
    elif re_abstime.match(s_time):
        YMD_hms = read_abstime(s_time)

    later_list = get_later_list(msg)
    with get_lock(*msg_id(msg)):
        for elem in later_list:
            if elem['seq'] == seq:
                elem['cancel']()
                elem.update({
                    'YMD_hms':YMD_hms,
                    'expr':expr,
                    'cancel':at(_read_YMD_hms(YMD_hms), _action, (seq, expr, msg)),
                })
                return YMD_hms

def print_list(later_list):
    return '\n'.join(map(lambda later:f"{later['seq']}: {later['YMD_hms']} {later['expr']}", sorted(later_list, key=lambda elem:elem['YMD_hms'])))

def is_safe(expr:str):
    if expr.startswith("'"):
        expr = expr.replace("\\'",'')
        if not "'" in expr[1:-1]:
            return True
    if expr.startswith('"'):
        expr = expr.replace('\\"','')
        if not '"' in expr[1:-1]:
            return True
    return False

def later_add(texts, msg):
    if len(texts)>=3 and re_abstime.match(s_time:=' '.join([texts[0], texts[1]])):
        expr = ' '.join(texts[2:])
    elif re_reltime.match(texts[0]):
        s_time, expr = texts[0], ' '.join(texts[1:])
    elif re_abstime.match(texts[0]):
        s_time, expr = texts[0], ' '.join(texts[1:])
    else:
        raise SyntaxError('时间格式不符')
    if not expr.strip():
        raise SyntaxError('表达式为空')
    if not msg['user_id'] in cache.ops and not is_safe(expr):
        return '字符串以外的任务需要管理员权限'
    seq, YMD_hms = enter(s_time, expr, msg)
    return f'{seq}: {YMD_hms} {expr}'

re_num_or = re.compile('^\d+(\s*,\s*\d+)*')
def run(text:str,exec_id=None):
    '''延时任务，简单字符串以外的表达式需要管理员权限
格式:
.later
.later
    : [add ]<time> <expr>
        <time>
            : [<int>(Y|y)][<int>M][<int>(D|d)][<int>h][<int>m][<int>s]
            | [<年:int>-]<月:int>-<日:int> <时:int>:<分:int>[:<秒:int>]
            | [<年:int>-]<月:int>-<日:int>
            | <时:int>:<分:int>[:<秒:int>]
        可以是相对时间或者绝对时间
        相对时间:
        add 1m30s '测试'
        add 43d60h 'foo'
        add 1Y2M33D1h30m60s 'bar'
        绝对时间:
        add 12:30 '中午好'
        add 5-22 6:00 '早啊'
        add 2023-5-20 4:00:00 '520'
    | del <seqs>
        举例:
        del 1,5,8 # 删除1,5,8号任务
        del * # 删除全部任务
    | set <seq:int> <time> <expr>'''
    msg = cache.thismsg()
    if not text.strip():
        # .later
        later_list = get_later_list()
        if not later_list:
            return '延时任务为空'
        return print_list(later_list)
    opr, text = read_params(text)
    try:
        if opr in ['-h','--help']:
            return run.__doc__
        elif opr=='add':
            texts = text.strip().split(' ')
            return later_add(texts, msg)
        elif opr=='del':
            text = text.strip()
            later_list = get_later_list()
            if text=='*':
                for later in later_list.copy():
                    cancel(later['seq'], msg)
                return '删除了全部计划任务'
            elif re_num_or.match(text):
                lst = sorted(list(set(map(int, map(str.strip, text.split(','))))))
                if len(lst)==1:
                    seq = lst[0]
                    YMD_hms, expr = cancel(seq, msg)
                    return f'{seq}: {YMD_hms} {expr}'
                dels = []
                for seq in lst:
                    YMD_hms, expr = cancel(seq, msg)
                    dels.append(f'{seq}: {YMD_hms} {expr}')
                return "\n".join(dels)
            else:
                raise SyntaxError('触发了del，但是格式错误了')
        elif opr=='set':
            seq, text = read_params(text)
            seq = int(seq)
            texts = text.strip().split(' ')
            if len(texts)>3 and re_abstime.match(s_time:=' '.join(texts[0], texts[1])):
                expr = ' '.join(texts[2:])
            elif re_reltime.match(texts[0]):
                s_time, expr = texts[0], ' '.join(texts[1:])
            elif re_abstime.match(texts[0]):
                s_time, expr = texts[0], ' '.join(texts[1:])
            else:
                raise SyntaxError('时间格式不符')
            if not expr.strip():
                raise SyntaxError('表达式为空')
            if exec_id is None:
                exec_id = msg['user_id']
            if not exec_id in cache.ops and not is_safe(expr):
                return '字符串以外的任务需要管理员权限'
            YMD_hms = change(seq, s_time, expr, msg)
            if YMD_hms is None:
                return '没有找到任务'
            return f'{seq}: {YMD_hms} {expr}'
        else:
            texts = [opr] + text.strip().split(' ')
            return later_add(texts, msg)
    except Exception as e:
        return traceback.format_exc()
