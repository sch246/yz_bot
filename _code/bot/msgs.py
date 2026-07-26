'''msg判断相关的函数'''
from main import cq


def haskeys(dic:dict, lst:list):
    return all(k in dic.keys() for k in lst)

evt = ['time','self_id','post_type']
def is_evt(msg:dict):
    return haskeys(msg, evt)


def is_heartbeat(msg:dict):
    return 'meta_event_type' in msg.keys() and msg['meta_event_type'] == 'heartbeat'
def is_connected(msg:dict):
    return 'meta_event_type' in msg.keys() and msg['meta_event_type'] == 'lifecycle' and 'sub_type' in msg.keys() and msg['sub_type']=='connect'

def is_api(msg):
    return 'retcode' in msg.keys()
def not_api(msg):
    return 'retcode' not in msg.keys()

# post_type = message
evt_msg = evt + ['sub_type','message_id','user_id','message','raw_message','font','sender']
def is_msg(msg:dict):
    '''消息'''
    return 'message' in msg.keys()
def is_group_msg(msg:dict):
    '''群消息'''
    return 'group_id' in msg.keys()
def is_friend(msg:dict):
    '''好友消息'''
    return msg['sub_type'] == 'friend'
def is_anonymous(msg:dict):
    '''匿名消息'''
    return msg['sub_type'] == 'anonymous'
def is_cq(msg):
    '''单个cq消息'''
    if is_msg(msg):
        s = msg['message']
        return cq.find_all(s)==[s]
def is_img(msg):
    '''单个img消息'''
    if is_cq(msg):
        s = msg['message']
        return cq.load(s)['type']=='image'
def is_reply(msg):
    return 'reply_cq' in msg.keys()



# post_type = request
evt_req = evt + ['request_type']
def is_req(msg:dict):
    return 'request_type' in msg.keys()
def is_friend_req(msg:dict):
    '''加好友请求'''
    return is_req(msg) and msg['request_type'] == 'friend'
def is_group_req(msg:dict):
    '''加群请求'''
    return is_req(msg) and msg['request_type'] == 'group'


# post_type = notice
evt_notice = evt + ['notice_type']
def is_notice(msg:dict):
    return 'notice_type' in msg.keys()
def is_file(msg:dict):
    '''可以是群文件或者离线文件'''
    if is_msg(msg) and is_cq(msg):
        CQ = cq.find_all(msg['message'])[0]
        d = cq.load(CQ)
        if d['type']=='file':
            return True
    return False

def is_group_file(msg:dict):
    '''群文件'''
    return msg.get('notice_type') == 'group_upload'
def is_private_file(msg:dict):
    '''离线文件'''
    return msg.get('notice_type') == 'offline_file'
def is_change_admin(msg:dict):
    '''管理员变动'''
    return msg.get('notice_type') == 'group_admin'
def is_leave(msg:dict):
    '''群成员减少'''
    return msg.get('notice_type') == 'group_decrease'
def is_join(msg:dict):
    '''群成员增多'''
    return msg.get('notice_type') == 'group_increase'
def is_ban(msg:dict):
    '''群禁言'''
    return msg.get('notice_type') == 'group_ban'
def is_group_recall(msg:dict):
    '''群撤回'''
    return msg.get('notice_type') == 'group_recall'
def is_friend_recall(msg:dict):
    '''好友撤回'''
    return msg.get('notice_type') == 'friend_recall'
def is_recall(msg:dict):
    return is_group_recall(msg) or is_friend_recall(msg)
def is_newfriend(msg:dict):
    '''新加好友提醒'''
    return msg.get('notice_type') == 'friend_add'

def is_card_new(msg:dict):
    '''群成员名片更新'''
    return 'card_new' in msg.keys()

def is_notify(msg:dict):
    return is_notice(msg) and msg['notice_type'] == 'notify'
def is_poke(qq=None):
    if isinstance(qq, dict):
        msg = qq
        return is_notice(msg) and msg.get('sub_type')=='poke'
    def _(msg):
        return is_notice(msg) and msg.get('sub_type')=='poke' and msg.get('target_id')==qq
    return _
def is_lucky_king(msg:dict):
    '''红包运气王'''
    return is_notify(msg) and msg['sub_type'] == 'lucky_king'
def is_honor(msg:dict):
    '''群荣誉变更'''
    return 'honor_type' in msg.keys()

def get_honor(honor_type:str):
    return {'talkative':'龙王', 'performer':'群聊之火', 'emotion':'快乐源泉'}[honor_type]

def is_client_status(msg:dict):
    '''客户端状态变化'''
    return 'client' in msg.keys()
def is_essence(msg:dict):
    '''精华消息'''
    return is_notice(msg) and msg['notice_type'] == 'essence'


# post_type = meta_event
evt_meta = evt + ['meta_event_type']
