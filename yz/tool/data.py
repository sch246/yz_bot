import json

uri = "ws://localhost:6700"

testmsg = json.dumps({
    "action": "send_msg",
    "params": {
        "user_id": "980001119",
        "message": "测试结果"
    }
})



event_keys = [  # 这里的方括号表示该项是个字典
    'time',  # 事件发生的时间戳,int64
    'self_id',  # 收到事件的机器人 QQ 号,int64
    {
        'post_type': {  # 上报类型, 若post_type的值是xx则会带来xx下列表的选项
                'message': [
                    'message_type',  # 消息类型,可选'privat'e或'group'
                    'sub_type',  # 消息子类型，个人:好友则是 friend，群临时会话则是 group，其余是other
                    # 群:正常消息是 normal，匿名消息是 anonymous，系统提示是 notice
                    'message_id',  # 消息ID，int32
                    ['group_id'],  # 群号,int64, 当message_type为group时方括号内的项存在
                    'user_id',  # 发送者 QQ 号, int64
                    [{'anonymous': [  # 这里的方括号表示该项是个字典
                        'id',  # 匿名用户 ID,int64
                        'name',  # 匿名用户名称, string
                        'flag'  # 匿名用户 flag, string，在调用禁言 API 时需要传入
                    ]}],
                    'message',  # 消息内容，可能不是string
                    'raw_message',  # 原始消息内容，string
                    'font',  # 字体，int32
                    {'sender': [  # 这里的方括号表示该项是个字典
                        'user_id',  # 发送者 QQ 号,int64，上面已有
                        'nickname',  # 昵称,string
                        ['card'],  # 群名片/备注, string
                        'sex',  # 性别, string,'male','female','unknow'
                        'age',  # 年龄， int32
                        ['area'],  # 地区
                        ['level'],  # 成员等级
                        ['role'],  # 角色,'owner' , 'admin' 或 'member'
                        ['title']  # 专属头衔
                    ]}
                ],
            'notice':{},
            'request': {},
            'meta_event': {}
        }
    }
]


example_log = {
        'group':{
            '1234567':[
                '群聊> 1234567 | awa(1234567): 草 (100165415)',
                '群聊> 1234567 | awa(1234567): c (1929336753)'
                ]
        },
        'private':{
            '980001119(康康)':[
                '私聊> 康康(980001119): .py out=1 (-358341012)',
                '私聊> 康康(980001119): .py out=2 (-2102235858)'
            ]
        }
    }


default_head = '\n'*3 + '[HEAD $start to $end] ' +'\n'




cq_trans_dic={
    '&':'&amp;',
    '[':'&#91;',
    ']':'&#93;',
    ',':'&#44;'
}
cq_load_dic = {v:k for k,v in cq_trans_dic.items()}

re_need_trans=['\\', '*', '.', '?', '+', '^', '$', '|',  '/', '[', ']', '(', ')', '{', '}', ]