'''修改bot post的端口地址(测试用)'''
import re

from main import connect, cache, read_params

post_map = connect.post_map
re_int = re.compile(r'(\d+)$')
re_g = re.compile(r'g(\d+)$')
re_u = re.compile(r'u(\d+)$')

def run(body: str):
    '''修改bot post的端口地址(测试用)，如果不知道它有什么用请不要随意调用
格式：
.post [<port:int>] 设置当前聊天（群聊或私聊）端口，或者重置（置空）
以下需要管理员权限
.post * 重置所有聊天端口
.post u<user_id:int> [<port:int>] 设置用户端口，或者重置（置空）
.post g<group_id:int> [<port:int>] 设置群聊端口，或者重置（置空）'''
    msg = cache.thismsg()
    user_id = msg.get('user_id')
    group_id = msg.get('group_id')
    s, last = read_params(body)
    if s=='' or re_int.match(s):
        return setport(s, group_id, user_id)
    if s == '*' or re_g.match(s) or re_u.match(s):
        if not user_id in cache.ops:
            if not cache.any_same(msg, r'\.post'):
                return '权限不足(一定消息内将不再提醒)'
            return None
        if s == '*':
            post_map.clear()
            return "所有端口已重置"
        if m:=re_g.match(s):
            group_id = int(m.group(1))
            s, last = read_params(last)
            if s=='' or re_int.match(s):
                return setport(s, group_id, None)
        if m:=re_u.match(s):
            user_id = int(m.group(1))
            s, last = read_params(last)
            if s=='' or re_int.match(s):
                return setport(s, None, user_id)
    return run.__doc__

def setport(s, group_id, user_id):
    if s=='':
        if group_id is not None:
            if f'g{group_id}' in post_map:
                del post_map[f'g{group_id}']
            return "端口已重置"
        elif user_id is not None:
            if f'u{user_id}' in post_map:
                del post_map[f'u{user_id}']
            return "端口已重置"
        else:
            return None
    if re_int.match(s):
        port = int(s)
        if group_id is not None:
            post_map[f'g{group_id}'] = port
            return f"端口已设置为 {port}"
        elif user_id is not None:
            post_map[f'u{user_id}'] = port
            return f"端口已设置为 {port}"
        else:
            return None
    return None
