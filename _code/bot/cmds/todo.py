'''计划任务'''


import time
import re
from typing import Iterable

import traceback

from main import to_thread, send
from main import storage, read_params, cache

def _format(i:int, todo:dict):
    return f'{i}: {todo.get("cond")} {todo.get("expr")}'

re_num_to = r'\d+(\-\d+)?'
re_num_or = rf'{re_num_to}(,{re_num_to})*'
re_cond = rf'^( \-)|( \*(\*|{re_num_or}))?( (\*|{re_num_or})){{1,5}}' # -|周?年月日时分

def _read_cond(text: str):
    '''读取cond，支持cron表达式'''
    print('text:', repr(text))
    cond = text.strip()
    validate_cron_expr(cond)  # 验证Cron表达式是否合法
    return cond, ''
def _read_cond(text:str):
    '''读取cond'''
    print('text:', repr(text))
    validate_cron_expr(cond)
    cond = re.match(re_cond, text)
    if not cond:
        raise SyntaxError('语法不符，未通过正则表达式')
    cond = cond.group()
    return cond.strip(), text.lstrip(cond).strip()

def _read_range(_range:str) -> Iterable[int]:
    for _r in _range.split(','):
        if '-' in _r:
            _from, _to = map(int, _r.split('-'))
            for i in range(_from, _to+1):
                yield i
        else:
            yield int(_r)

def run(text:str):
    '''计划任务，每个人或者每个群的计划任务是分离的
每秒钟过一遍计划任务的判定，通过则执行表达式并返回
格式:
.todo
.todo
    : add <cond> <expr>
        <cond>: - | [*周 ][[[[年 ]月 ]日 ]时 ]分
        *表示通配符，因为星期本身有一个*，所以星期的通配是**
        当前面都是通配符时，可以省略
        举例:
        add */7 'foo' # 每7分钟发送一次'foo'
        add 0 'awa' # 整点发送'awa'
        add *1 0 'awa' # 仅周一的整点发送'awa'
        add - 'awa' # 永不触发
    | del <range>
        举例:
        del 1,5,8 # 删除1,5,8号任务
        del 3-8,11 # 删除3~8号任务，和11号
        del * # 删除全部任务
    | set <index:int> <cond> <expr>
    | move <src:int> <tar:int>'''
    todo_list = get_todo_list()
    if not text.strip():
        # .todo
        if not todo_list:
            return '计划任务为空'
        return '\n'.join([_format(i, todo)
                          for i, todo in enumerate(todo_list)])
    op, text = read_params(text)
    try:
        if op=='add':
            cond, expr = _read_cond(text)
            todo = {
                'cond': cond,
                'expr': expr,
            }
            todo_list.insert(0, todo)
            return _format(0, todo)
        elif op=='del':
            s_range, text = read_params(text)
            if text: raise SyntaxError('触发了del，输入了多余的参数')
            s_range = s_range.strip()
            if s_range=='*':
                todo_list.clear()
                return '删除了全部计划任务'
            elif re.match(re_num_or, s_range):
                count = []
                for i in sorted(list(set(_read_range(s_range))),reverse=True):
                    if i in range(len(todo_list)):
                        todo_list.pop(i)
                        count.insert(0,str(i))
                return f'删除了 {",".join(count)}'
            else:
                raise SyntaxError('触发了del，参数数量正确，但是格式错误了')
        elif op=='set':
            i, text = read_params(text)
            cond, expr = _read_cond(text)
            i = int(i)
            todo = {
                'cond': cond,
                'expr': expr,
            }
            todo_list[i] = todo
            return _format(i, todo)
        elif op=='move':
            i, j, text = read_params(text, 2)
            i, j = int(i), int(j)
            todo_list.insert(j, todo_list.pop(i))
            return _format(j, todo_list[j])
        else:
            return run.__doc__
    except Exception as e:
        print(traceback.format_exc())
        return run.__doc__

storage.storage.setdefault('todo_list/groups',{})
storage.storage.setdefault('todo_list/users',{})

def get_todo_list() -> list:
    msg = cache.thismsg()
    if 'group_id' in msg:
        return storage.get('todo_list/groups', str(msg['group_id']), list)
    else:
        return storage.get('todo_list/users', str(msg['user_id']), list)

import re
import time
from typing import Iterable

# Cron格式正则表达式
cron_pattern = r'^(?:\*|([0-9]|[1-5][0-9])|([0-9]|[1-2][0-9]|3[0-1])|([1-9]|1[0-2])|([0-6]))(\/([0-9]+))?$'

# 任务条件验证函数
def validate_cron_expr(cron_expr: str) -> bool:
    # 基本的Cron表达式规则检查
    parts = cron_expr.split(' ')
    if len(parts) != 5:
        raise SyntaxError(f"Invalid cron expression: {cron_expr}, it should have exactly 5 fields.")

    # 分钟, 小时, 日期, 月份, 星期 (Cron format)
    for i, part in enumerate(parts):
        # 检查每一部分的合法性
        if not re.match(cron_pattern, part):
            raise SyntaxError(f"Invalid cron field {i}: {part}")

    return True




from datetime import datetime

# 当前时间验证
def _check(cond: str) -> bool:
    now = datetime.now()
    cron_parts = cond.split(' ')

    # 分钟, 小时, 日期, 月份, 星期
    minute_expr, hour_expr, day_expr, month_expr, weekday_expr = cron_parts

    # 分别检查每个字段
    if not _match_field(minute_expr, now.minute, 0, 59): return False
    if not _match_field(hour_expr, now.hour, 0, 23): return False
    if not _match_field(day_expr, now.day, 1, 31): return False
    if not _match_field(month_expr, now.month, 1, 12): return False
    if not _match_field(weekday_expr, now.weekday(), 0, 6): return False

    return True

def _match_field(expr: str, current_value: int, min_value: int, max_value: int) -> bool:
    '''检查表达式是否与当前值匹配'''
    if expr == '*':
        return True  # *表示匹配任何值
    
    # 支持类似*/5，5,10-15等
    if '/' in expr:
        base, step = expr.split('/')
        step = int(step)
        return current_value % step == 0
    
    # 支持类似1,5-10等
    if '-' in expr:
        start, end = map(int, expr.split('-'))
        return start <= current_value <= end
    
    # 直接匹配数字
    if expr.isdigit():
        return current_value == int(expr)

    return False

# 执行任务函数
def execute_task(expr: str, group_id: str = None, user_id: str = None):
    """根据任务表达式执行任务"""
    try:
        # 执行表达式 (需要根据任务来修改为合适的行为)
        result = eval(expr)  # 请谨慎使用eval，确保表达式是可信的
        # 发送消息
        if group_id:
            send(result, group_id=group_id)
        elif user_id:
            send(result, user_id=user_id)
    except Exception as e:
        print(f"Error executing task: {e}")


@to_thread
def _loop():
    while True:
        for group_id, todo_list in storage.storage['todo_list/groups'].items():
            for todo in todo_list:
                cond, expr = todo.get('cond'), todo.get('expr')
                if _check(cond):
                    execute_task(expr, group_id=group_id)
        
        for user_id, todo_list in storage.storage['todo_list/users'].items():
            for todo in todo_list:
                cond, expr = todo.get('cond'), todo.get('expr')
                if _check(cond):
                    execute_task(expr, user_id=user_id)
        
        # 每分钟执行一次任务
        time.sleep(60)

_loop()
