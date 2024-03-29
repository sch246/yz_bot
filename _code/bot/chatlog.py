'''用于分类记录QQ聊天记录，每条消息会被存到2个地方，可查看文件，以及缓存'''

import time
from os.path import join


# from main import str_tool, params, file, in_debug, cache, cq, msgs

from main import *


rootfile = 'chatlog'


def write(msg):
    print(f'{msg}')
    t = msg['time']
    try:
        if is_msg(msg):
            text = cq.unescape(msg['message'])
            if 'sender' in msg.keys() and 'user_id' in msg['sender'].keys():
                cache.update(msg)
            else:
                msg['sender'] = {'user_id':msg['user_id']}
            if is_group_msg(msg):
                # title可能来自于专属昵称，也可能来自于等级，但是等级似乎无法获得
                # name可能来自于群昵称，也可能来自于QQ名
                user_id = int(msg['sender']['user_id'])
                group_id = int(msg['group_id'])
                title, name = cache.get_group_user_info(group_id, user_id)
                return _group_write(msg, group_id, _group_str(title, name, user_id, t, text, msg['message_id']))
            else:
                user_id = int(msg['user_id'])
                name = cache.get_user_name(int(msg['sender']['user_id']))
                return _private_write(msg, user_id, _private_str(name, t, text, msg['message_id']))
        elif is_notice(msg):
            if 'operator_id' in msg.keys():
                msg['user_id'] = msg['operator_id']
            if 'user_id' in msg.keys():
                user_id = int(msg['user_id'])
                name = cache.get_user_name(user_id)
            if 'group_id' in msg.keys():
                group_id = int(msg['group_id'])
                title, name = cache.get_group_user_info(group_id, user_id)
            if is_group_file(msg):
                return _group_write(msg, group_id, _group_str(title, name, user_id, t, _file_str(msg['file']), ''))
            elif is_private_file(msg):
                if 'sender' in msg.keys() and 'user_id' in msg['sender'].keys():
                    name = cache.get_user_name(int(msg['sender']['user_id']))
                return _private_write(msg, user_id, _private_str(name, t, _file_str(msg['file']), ''))
            elif is_change_admin(msg):
                if msg['sub_type']=='set':
                    text = f'{name}({user_id})被设为了管理员'
                else:
                    text = f'{name}({user_id})被移除了管理员'
                return _group_write(msg, group_id, _notice_str(t, text))
            elif is_leave(msg):
                if msg['sub_type']=='leave':
                    text = f'{name}({user_id})离开了群'
                else:
                    if msg['sub_type']=='kick_me':
                        user_id = cache.get('qq')
                    operator_id = int(msg['operator_id'])
                    _, operator = cache.get_group_user_info(group_id, operator_id)
                    text = f'{name}({user_id})被{operator}({operator_id})踢出了群'
                return _group_write(msg, group_id, _notice_str(t, text))
            elif is_join(msg):
                operator_id = int(msg['operator_id'])
                _, operator = cache.get_group_user_info(group_id, operator_id)
                if msg['sub_type']=='approve':
                    text = f'{operator}({operator_id})同意{name}({user_id})加入了群'
                else:
                    text = f'{operator}({operator_id})邀请{name}({user_id})加入了群'
                return _group_write(msg, group_id, _notice_str(t, text))
            elif is_ban(msg):
                operator_id = int(msg['operator_id'])
                _, operator = cache.get_group_user_info(group_id, operator_id)
                if msg['sub_type']=='ban':
                    day, hour, minu, sec =  gettime(msg['duration'])
                    text = f'{name}({user_id})被{operator}({operator_id})禁言{day}天{hour}时{minu}分{sec}秒'
                else:
                    text = f'{name}({user_id})被{operator}({operator_id})解除禁言'
                return _group_write(msg, group_id, _notice_str(t, text))
            elif is_newfriend(msg):
                return _bot_write(msg, _notice_str(t, f'添加了{cache.get_user_name(user_id)}({user_id})为好友'))
            elif is_group_recall(msg):
                msg_id = msg['message_id']
                operator_id = int(msg['operator_id'])
                _, operator = cache.get_group_user_info(group_id, operator_id)
                if operator_id==msg['user_id']:
                    text = f'{name}({user_id})撤回了一条消息({msg_id})'
                else:
                    text = f'{operator}({operator_id})撤回了{name}({user_id})的一条消息({msg_id})'
                return _group_write(msg, group_id, _notice_str(t, text))
            elif is_friend_recall(msg):
                return _private_write(msg, user_id, _notice_str(t, f'{cache.get_user_name(user_id)}撤回了一条消息({msg["message_id"]})'))
            elif is_poke(msg):
                target_id = int(msg['target_id'])
                if 'group_id' in msg.keys():
                    _, target = cache.get_group_user_info(group_id, target_id)
                    text = f'{name}({user_id})戳了戳{target}({target_id})'
                    return _group_write(msg, group_id, _notice_str(t, text))
                else:
                    target = cache.get_user_name(target_id)
                    text = f'{name}戳了戳{target}'
                    return _private_write(msg, user_id, _notice_str(t, text))
            elif is_lucky_king(msg):
                target = cache.get_user_name(int(msg['target_id']))
                return _group_write(msg, group_id, _notice_str(t, f'{name}({user_id})的红包，{target}({target_id})是运气王'))
            elif is_honor(msg):
                honor = get_honor(msg['honor_type'])
                return _group_write(msg, group_id, _notice_str(t, f'{name}({user_id}) 获得荣誉: {honor}'))
            elif is_card_new(msg):
                if msg["card_new"]:
                    return _group_write(msg, group_id, _notice_str(t, f'{name}({user_id})更新了ta的名片为"{msg["card_new"]}"'))
                else:
                    return _group_write(msg, group_id, _notice_str(t, f'{name}({user_id})移除了ta的名片'))
            elif is_essence(msg):
                msg_id = msg['message_id']
                operator_id = int(msg['operator_id'])
                _, operator = cache.get_group_user_info(group_id, operator_id)
                if msg["sub_type"] == 'add':
                    return _group_write(msg, group_id, _notice_str(t, f'{operator}({operator_id})将{name}({user_id})的消息({msg_id})设为了精华消息'))
                else:
                    return _group_write(msg, group_id, _notice_str(t, f'{operator}({operator_id})取消了{name}({user_id})的精华消息消息({msg_id})'))
        elif is_req(msg):
            if is_friend_req(msg):
                return _bot_write(msg, _private_str(f'{cache.get_user_name(user_id)}({user_id})请求添加你为好友',t, msg['comment']))
        else:
            return _bot_write(msg, _private_str('其它消息',t, f'{msg}'))
    except:
        return _bot_write(msg, _private_str('未捕获消息',t, f'{msg}'))


def _group_write(msg, uid, text:str):
    t = msg['time']
    filepath = get_path(join(rootfile, 'group',str(uid)), t)
    file.add(filepath, text)
    cache.add_msg('group', uid, msg)
    return text
def _private_write(msg, uid, text:str):
    t = msg['time']
    filepath = get_path(join(rootfile, 'private',str(uid)), t)
    file.add(filepath, text)
    cache.add_msg('private', uid, msg)
    return text
def _bot_write(msg, text:str):
    t = msg['time']
    filepath = get_path(join(rootfile, 'bot'), t)
    file.add(filepath, text)
    cache.add_self_msg(msg)
    return text

# def _write_str(type: str, uid: int, t: int, text: str):
#     f = params.append(time.strftime, [time.localtime(t)])
#     if uid is None:
#         dirpath = join(rootfile, type, f(r"%Y-%m"))
#     else:
#         dirpath = join(rootfile, type, str(uid), f(r"%Y-%m"))
#     filepath = join(dirpath, f(r"%d.log"))
#     if not os.path.isdir(dirpath):
#         os.makedirs(dirpath)
#     with open(filepath, 'a', encoding='utf-8') as f:
#         f.write(text)
#     return text


def get_path(root:str, t:int):
    f = params.append(time.strftime, [time.localtime(t)])
    return file.ensure_file(join(root, f(r"%Y-%m"), f(r"%d.log")))
    # dirpath = join(root, f(r"%Y-%m"))
    # filepath = join(dirpath, f(r"%d.log"))
    # if not os.path.isdir(dirpath):
    #     os.makedirs(dirpath)
    # return filepath


def _file_str(file):
    return f'''【文件】{file['name']} {_get_size(file['size'])}
{file['url']}'''



def _get_size(byte:int):
    byte_size = ['B', 'KB', 'MB', 'GB', 'TB']
    for name in byte_size:
        if byte < 1000:
            return f'{byte:.2f}{name}'
        byte = byte / 1024


def _group_str(title: str, name: str, user_id: int, t: int, text: str, msg_id:int):
    return f'【{title}】{name}({user_id}) {time.strftime("%H:%M:%S", time.localtime(t))} | {msg_id}\n{str_tool.addtab(text)}\n'


def _private_str(name: str, t: int, text: str, msg_id:str=''):
    if msg_id:
        msg_id = f' | {msg_id}'
    return f'{name} {time.strftime(r"%H:%M:%S", time.localtime(t))}{msg_id}\n{str_tool.addtab(text)}\n'


def _notice_str(t: int, text: str):
    return f': {text} {time.strftime(r"%H:%M:%S", time.localtime(t))}\n'


def gettime(sec:int):
    day = sec // 86400
    sec %= 86400
    hour = sec // 3600
    sec %= 3600
    minu = sec // 60
    sec %= 60
    return day, hour, minu, sec

if __name__ == "__main__":
    write({
        "anonymous": None,
        "font": 0,
        "group_id": 916083933,
        "message": "（（~",
        "message_id": -1122407633,
        "message_seq": 643019,
        "message_type": "group",
        "post_type": "message",
        "raw_message": "（（~",
        "self_id": 1581186041,
        "sender": {
            "age": 0,
            "area": "",
            "card": "",
            "level": "",
            "nickname": "蜂蜜柚子茶",
            "role": "member",
            "sex": "unknown",
            "title": "",
            "user_id": 236288772
        },
        "sub_type": "normal",
        "time": 1658807341,
        "user_id": 236288772
    })
