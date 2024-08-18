'''gpt3.5'''

import time
import re
from datetime import datetime
from io import StringIO
import traceback
from typing import Any, Callable
import requests
from urllib.request import quote, unquote
from termcolor import colored

import openai
from openai.types.chat.chat_completion_message import ChatCompletionMessage
import tiktoken


from main import sendmsg
from main import cache, msg_id, storage, str_tool
from main import settings, getchatstorage, chat_groups
from main import is_msg, is_poke, has_at, find, getlog, msg2chat, chat2msg, getcmd, getgroupname, getname, lunar_time, 小六壬, sendmsg as _sendmsg
from main import cq
from main import CommandManager

import json

from main import Chat, MessageStream

from main import memberlist

chat_client = Chat()

encoding = tiktoken.encoding_for_model('gpt-4')

def count_tokens(text:str):
    return len(encoding.encode(text))

prices = {
    "gpt-3.5-turbo":(0.0035, 0.0105),
    "gpt-4":(0.21, 0.42),
    "gpt-4o":(0.035, 0.105),
    "claude-3-5-sonnet-20240620":(0.012, 0.06),
}

def get_caller():
    msg = cache.thismsg()
    qq = str(msg['user_id'])
    mon = f'{datetime.today().month}'
    usage:dict = storage.get('usage', mon)
    usage.setdefault(qq, [0,0])
    return usage[qq]

def inc_call_count():
    get_caller()[0] += 1

def inc_call_token_cost(model, type:int, token:int):
    price = token*prices[model][type]/1000
    get_caller()[1] += price

def inc_call_text_cost(model, type:int, text:str):
    token = count_tokens(text)
    inc_call_token_cost(model, type, token)

def inc_call_image_cost(size:str, quality:str):
    price = 0.28
    if size != "1024x1024":
        price += 0.28
    if quality == 'hd':
        price += 0.28
    get_caller()[1] += price

def get_time():
    '''
    获取当前时间
    '''
    return f'现在是{time.strftime("%Y年%m月%d日%H时%M分%S秒")}'

def group_state(s=None):
    group_id = cache.thismsg().get('group_id')
    if group_id:
        return f'当前所在群聊:{getgroupname(group_id)}({group_id})'
    else:
        user_id = cache.thismsg().get('user_id')
        return f'当前在私聊:{getname(user_id)}({user_id})'

def group_size():
    '''
    获取当前群的群员数量
    '''
    return str(len(memberlist()))
def group_members():
    '''
    获取当前群的群员列表
    '''
    return '\n'.join([f'{getname(msg["user_id"])}({msg["user_id"]}) 名片:"{msg["title"]}" sex:{msg["sex"]}' for msg in memberlist()])

def exec_code(expr:str,code:str=''):
    '''
    execute a real-time python code.
    你应该可以用python读取和编辑`data`字典，其中的数据会被持久化保存
    保存时尽可能使用问句作为键名

    @param
    code: The code to execute

    expr: The value to be returned, eval after the code execute
    '''
    dic = getcmd('py').loc
    exec(code,dic)
    return repr(eval(expr,dic))

def read_data(key:str):
    '''
    按key读取data内的内容

    @param
    key: 所查询的key
    '''
    data = getcmd('py').loc['data']
    return data.get(key,'没有找到内容')


def lunar_date():
    '''
    获取农历
    '''
    return str(lunar_time())

def xiaoliu():
    '''
    算当前的小六壬
    '''
    return 小六壬()

def sendmsg(text:str, user_id:int=None, group_id:int=None):
    '''
    发送消息到其它群聊(group_id)或者其它私聊(user_id)

    @param
    text: 将要发送的消息

    user_id: 将要私聊的用户qq号，与group_id冲突

    group_id: 将要发消息的群聊，与user_id冲突
    '''
    if user_id is None and group_id is None:
        msg = cache.thismsg()
        group_id = msg.get('group_id')
        user_id = msg.get('user_id')
    _sendmsg(text, user_id=user_id, group_id=group_id)
    return '已发送'

def later_list():
    '''
    列出当前延时任务
    返回值为延时任务(`<seq>: <%Y-%m-%d %H:%M:%S> <python expr>`)的列表
    '''
    return getcmd('later').run('', exec_id=cache.qq)
def later_add(time:str, code:str, expr:str):
    '''
    添加延时任务
    在设置的时间到达时, 在对应会话执行python代码，并发送表达式的值
    例如，如果用户要求一分钟后提醒他上厕所，则 time="1m", code="", expr="'该上厕所啦~'"
    返回值为`<seq>: <%Y-%m-%d %H:%M:%S> <python expr>`

    @param
    time: 延时时间, 可以是相对时间或者绝对时间, 相对时间的例子:`1m30s`,`43d60h`,`1Y2M33D1h30m60s`;绝对时间的例子:`12:30`,`5-22 6:00`,`2023-5-20 4:00:00`
    code: 到时间时，执行的python代码
    expr: 到时间时，被作为消息发送的python表达式，例子:`'该吃饭啦'`,`'当前时间是:'+get_time()`,`sendmsg('你好啊', user_id=<设置为对应用户的qq号>)`,`sendmsg('大家好', group_id=<设置为对应群聊的qq号>)`
    '''
    return getcmd('later').run(f' add {time} {code}\n{expr}', exec_id=cache.qq)

def later_del(seqs:str):
    '''
    按序号删除延时任务
    返回值为删除的延时任务(`<seq>: <%Y-%m-%d %H:%M:%S> <python expr>`)的列表

    @param
    seqs: 延时任务的序号，例如`2`，删除多个时用逗号隔开,不要空格，例如`1,5,8`，删除全部任务(谨慎使用)是`*`
    '''
    return getcmd('later').run(f' del {seqs}', exec_id=cache.qq)

def later_set(seq:str, time:str, code:str, expr:str):
    '''
    按序号修改延时任务
    返回值为修改后的延时任务`<seq>: <%Y-%m-%d %H:%M:%S> <python expr>`

    @param
    seq: 延时任务的序号
    time: 延时时间, 可以是相对时间或者绝对时间, 相对时间的例子:`1m30s`,`43d60h`,`1Y2M33D1h30m60s`;绝对时间的例子:`12:30`,`5-22 6:00`,`2023-5-20 4:00:00`
    code: 到时间时，执行的python代码
    expr: 到时间时，被作为消息发送的python表达式，例子:`'该吃饭啦'`,`'当前时间是:'+get_time()`,`sendmsg('你好啊', user_id=<设置为对应用户的qq号>)`,`sendmsg('大家好', group_id=<设置为对应群聊的qq号>)`
    '''
    return getcmd('later').run(f' set {seq} {time} {expr}', exec_id=cache.qq)

def url2cq(url:str):
    '''
    Convert urls to cq codes

    @param
    url: Image url
    '''
    return cq.url2cq(url)


def create_image(prompt:str, size:str, quality:str):
    '''
    Create an image based on the description and return the cq code, pictures are automatically sent
    Use the standard parameter whenever possible unless explicitly requested by the user

    @param
    prompt: Description text used to create the image
    size: Picture size
        enum: ["1024x1024", "1024x1792", "1792x1024"]
    quality: Image quality
        enum: ["standard", "hd"]
    '''
    inc_call_image_cost(size, quality)
    picCQ = url2cq(chat_client.create_image(prompt, size, quality))
    sendmsg(picCQ)
    return picCQ

def baidu_encyclopedia(object:str):
    '''
    Search Baidu encyclopedia, more accurate for common sense problems

    @param
    object: The object you want to query
    '''
    url = quote(f'https://api.wer.plus/api/dub?t={object}', safe=";/?:@&=+$,", encoding="utf-8")
    res:dict[str,Any] = requests.get(url).json()
    if res['code']!=200:
        return '查询失败'
    return res['data']['text']


prompts = storage.get('', 'prompts')

def get_prompt() -> list|None:
    data = getchatstorage()
    name = data.get('prompt') #可能是name索引或者list
    if not name:
        return settings
    elif isinstance(name, list):
        return name
    elif name in prompts:
        return prompts[name]
    else:
        return None

def get_msgs(max_token=1_000, return_token=False):
    in_group = cache.thismsg().get('group_id')

    chat_logs = []
    for msg in getlog():
        if not is_msg(msg):
            continue
        if msg['message'].startswith('#'):
            continue
        if msg['message']=='聊天开始' or msg['message']=='聊天结束':
            break
        chat_logs.append(msg)

    messages = []

    sum_token = 0
    for msg in chat_logs:
        chat_msg = msg2chat(msg, in_group)

        content = chat_msg['content']
        if isinstance(content, str):
            sum_token += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if part.get('type')=='text':
                    sum_token += count_tokens(part['text'])
                elif part.get('type')=='image_url':
                    sum_token += part['token_cost']
                else:
                    print(f"chat error: 消息中有text和image_url之外的对象: {part}")
        else:
            print(f"chat error: 消息列表中有非列表非字符串的对象: {repr(content)}")

        if sum_token > max_token:
            break
        messages.insert(0, chat_msg)

    if return_token:
        return messages, sum_token
    return messages

def init_chat(chat_client:Chat):
    inc_call_count()
    # chat.add_tool(get_location)
    chat_client.add_tool(get_time)
    chat_client.add_tool(exec_code)
    # chat_client.add_tool(read_data)
    # chat_client.add_tool(group_size)
    # chat_client.add_tool(group_members)
    # chat_client.add_tool(lunar_date)
    # chat_client.add_tool(xiaoliu)
    # chat_client.add_tool(sendmsg)
    # chat_client.add_tool(later_list)
    chat_client.add_tool(later_add)
    # chat_client.add_tool(later_set)
    chat_client.add_tool(later_del)
    chat_client.add_tool(create_image)
    chat_client.add_tool(chat_client.read_image)
    # chat_client.add_tool(url2cq)
    # chat_client.add_tool(muti_reply)
    # chat_client.add_tool(baidu_encyclopedia)

    # chat.add(chat.req())

    # last_data = None
    # def show_data(s):
    #     data = getcmd('py').data
    #     global last_data
    #     result = '`data`内的键及类型: {'+', '.join([f"`{k}`: {type(v)}" for k, v in data.items()])+'}' if data!=last_data else None
    #     last_data = data.copy()
    #     return result

    data = getchatstorage()
    prompt = get_prompt()
    if not prompt:
        prompt = []

    chat_client.set_settings([
            *prompt,
            group_state,
            # show_data,
            # 'If necessary, remember to use python to read the storage in \'data\' at any time',
            # 'Images must be converted to cq code before they can be sent, do not use the markdown format',
            ])

    if 'split' not in data: # 设置默认值
        data['split'] = True
    chat_client.split = data['split'] #决定是否划分发送


cm = CommandManager()

@cm.register('split')
def _()->str:
    '''
    切换是否划分发送消息
    '''
    data = getchatstorage()
    data['split'] = not data.get('split')
    return f"split: {data['split']}"

@cm.register('use_model')
def _()->str:
    '''
    重置模型
    '''
    if getchatstorage().get('model'):
        del getchatstorage()['model']
    return '已重置模型'

@cm.register('use_model <model:str>')
def _(model:str)->str:
    '''
    设置模型
    '''
    getchatstorage()['model'] = model
    return f'模型设置为 {model}'

@cm.register('prompt')
def _()->list:
    '''
    查看当前提示词
    '''
    return getchatstorage()['prompt']

@cm.register('add_prompt')
def _()->str:
    '''
    追加上一句话到提示词
    '''
    chat_msgs = get_msgs()[-1:]
    data = getchatstorage()
    old_prompt = get_prompt()
    if old_prompt is None:
        return f"prompt 类型错误: 期望 str 或 list, 得到了 {type(old_prompt)}"
    else:
        data['prompt'] = old_prompt + chat_msgs
        return f'上一句聊天已追加到提示词'

@cm.register('add_prompt <count:int>')
def _(count:int)->str:
    '''
    以当前聊天追加提示词，填0追加全部
    '''
    chat_msgs = get_msgs()
    if count:
        chat_msgs = chat_msgs[-count:]
    data = getchatstorage()
    old_prompt = get_prompt()
    if old_prompt is None:
        return f"prompt 类型错误: 期望 str 或 list, 得到了 {type(old_prompt)}"
    else:
        data['prompt'] = old_prompt + chat_msgs
        return f'当前聊天已追加到提示词(注意重复)'

@cm.register('add_prompt <prompt:list>')
def _(prompt:list)->str:
    '''
    追加提示词
    '''
    data = getchatstorage()
    old_prompt = get_prompt()
    if old_prompt is None:
        return f"prompt 类型错误: 期望 str 或 list, 得到了 {type(old_prompt)}"
    else:
        data['prompt'] = old_prompt + prompt
        return f'提示词已追加'

@cm.register('use_setting')
def _()->str:
    '''
    重置提示词
    '''
    if getchatstorage().get('prompt'):
        del getchatstorage()['prompt']
    return '已重置提示词'

@cm.register('use_setting <name:str>')
def _(name:str)->str:
    '''
    应用设定
    '''
    if prompts.get(name):
        getchatstorage()['prompt'] = prompts[name]
        return '设定已应用'
    else:
        return '未找到设定，你可能需要先创建设定'

@cm.register('del_setting <name:str>')
def _(name:str)->str:
    '''
    删除设定
    '''
    del prompts[name]
    return '设定已删除'

@cm.register('set_setting <name:str>')
def _(name:str)->str:
    '''
    将当前的提示词保存到设定
    '''
    prompts[name] = getchatstorage()['prompt']
    return '已保存当前的提示词为设定'

@cm.register('set_setting <name:str> <prompt:list>')
def _(name:str, prompt:list)->str:
    '''
    创建或设置设定
    '''
    prompts[name] = prompt
    return '设定已保存'

@cm.register('get_setting <name:str>')
def _(name:str)->list|str:
    '''
    查找设定
    '''
    prompt = prompts.get(name)
    if prompt:
        return prompt
    else:
        return '未找到设定，你可能需要先创建设定'



# 作为条件调用，返回非空值(data)以触发结果(call(data))
def cond() -> Callable | bool:
    msg = cache.thismsg()

    # 如果是群聊且在没有开放的群聊中
    group_id = msg.get('group_id')
    if group_id and group_id not in chat_groups:
        return False

    if is_msg(msg):
        text:str = msg['message']
        if has_at(cache.qq)(msg) or text.startswith('柚子，'):
            return True
        elif text.startswith('#'):
            if text=='#poke':
                return True
            return cm.check(cq.unescape(text[1:]))
    elif is_poke(cache.qq)(msg):
        return True

    return False

def call(data: Callable | bool):

    # 如果是命令则执行
    if callable(data):
        return '#'+cq.escape(str(data()))

    return chat()

role_to_color = {
    "system": "red",
    "user": "green",
    "assistant": "blue",
    "tool": "magenta",
}
def show_args(args):
    return ', '.join([f'{k}={repr(v)}' for k, v in args.items()])
def show_tool_calls(tool_calls):
    return ''.join(map(lambda s:f'\n    {s["function"]["name"]}({show_args(json.loads(s["function"]["arguments"]))})', tool_calls))
def split_string_with_code_blocks(text:str):
    result = []
    count = 0

    for part in text.split('\n\n'):
        last_is_code = count%2==1
        count += part.startswith('```')+part.count('\n```')
        if last_is_code and count%2==1:
            result[-1] += '\n\n'+part
        else:
            result.append(part)

    return result
 
def pprint(message:dict | ChatCompletionMessage | MessageStream | str, model:str, split=True):
    '''
    打印 dict, 普通消息, 或者流式消息, 然后返回
    流式消息会转换为普通消息
    '''
    if isinstance(message, str):
        print(colored(f"system: {message}", "red"))
        _sendmsg('#error: '+message)
        return ChatCompletionMessage(role='system', content=message)
    elif isinstance(message, MessageStream):
        role = message.role
        tool_calls = message.tool_calls
        if message.tool_calls:
            print(colored(f"assistant called: {tool_calls[0].function.name} ", "yellow"),end='', flush=True)
            text:str = ''
            for delta in message:
                text += delta
                print(colored(delta, "yellow"),end='', flush=True)
            inc_call_text_cost(model, 1, text) #TODO 调用函数应该也算输出吧
        else:
            print(colored(f"assistant: ", role_to_color[role]),end='', flush=True)
            sum_text:str = ''
            text:str = ''
            for delta in message:
                print(colored(delta, role_to_color[role]),end='', flush=True)
                text += delta
                sum_text += delta
                if not split:
                    continue
                *parts, text = split_string_with_code_blocks(text)
                for part in parts:
                    inc_call_text_cost(model, 1, part)
                    _sendmsg(part)
            if text:
                inc_call_text_cost(model, 1, text)
                _sendmsg(text)
        print('\n')
        return message.msg
    else:
        if (isinstance(message,ChatCompletionMessage)):
            msg = message.dict()
        else:
            msg = message
        role = msg.get('role')
        tool_calls = msg.get('tool_calls')
        content = msg.get('content')
        name = msg.get('name')
        if role == "system":
            print(colored(f"system: {content}\n", role_to_color[role]))
        elif role == "user":
            pass
            # print(colored(f"user: {content}\n", role_to_color[role]))
        elif role == "assistant" and tool_calls:
            print(colored(f"assistant called: {show_tool_calls(tool_calls)}\n", "yellow"))
            #TODO 没想好应该怎么计算
        elif role == "assistant" and not tool_calls:
            print(colored(f"assistant: {content}\n", role_to_color[role]))
            inc_call_text_cost(model, 1, content)
        elif role == "tool":
            print(colored(f"function ({name}): {content}\n", role_to_color[role]))
        else:
            print('else:',msg)
        return message
def add(msg, chat_client:Chat):
    """
    Print, transform and add a message to the chat history, then return the transformed message
    流式消息会被转换为普通消息

    :param msg: The message to be added.
    :return: The added message.
    """
    msg = pprint(msg, chat_client.model, chat_client.split)
    chat_client.messages.append(msg)
    return msg

def chat(model=None):
    # call = [
    #     *settings,
    #     {'role': 'system', 'content':f'现在是{time.strftime("%Y年%m月%d日%H时%M分%S秒")}'}
    # ]
    # prompt_tokens = counts_token(call)

    if model is None:
        data = getchatstorage()
        if data.get('model', None):
            model = data['model']
        else:
            model = "claude-3-5-sonnet-20240620"

    chat_client = Chat(model=model)
    init_chat(chat_client)

    chat_client.messages, sum_token = get_msgs(return_token=True)

    # 增加计费
    inc_call_token_cost(model, 0, sum_token)

    # chats = []
    # while chat_contexts:
    #     message = chat_contexts.pop()
    #     if len(chats)<100:
    #         chats.insert(0, message)
    # if cache.get('debug_chat') and chat_contexts:
    #     print('#被截断的消息有', len(chat_contexts), '条。')
    # chats.append({'role':'assistant','content':''})

    # return chat_client.call().content
    tools = [v.description for v in chat_client.tools.values()]
    model = model if model is not None else chat_client.model
    try:
        stream = chat_client.req(tools, "auto", model)
    except StopIteration:
        _sendmsg('# 模型返回了空消息')
        return
    res_msg = add(stream, chat_client)
    tool_calls = res_msg.tool_calls
    while tool_calls:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            try:
                content = chat_client.tools[function_name].call(**json.loads(tool_call.function.arguments))
            except:
                content =f'called: {json.loads(tool_call.function.arguments)}\n\n'+ traceback.format_exc()
            add({
                "role": "tool",
                "name": function_name,
                "content": content,
                "tool_call_id": tool_call.id,
            }, chat_client)
        try:
            stream = chat_client.req(tools, "auto", model)
        except StopIteration:
            _sendmsg('# 模型返回了空消息')
            return
        res_msg = add(stream, chat_client)
        tool_calls = res_msg.tool_calls

def _rm_pre_text(text:str):
    name = cache.nicknames[0]
    pre = f'[{name}]({cache.qq}): '
    if text.startswith(pre):
        return text.lstrip(pre)
    return text

def process_res(res):
    text = StringIO()
    for chunk in res:
        delta = chunk['choices'][0]['delta']
        char = delta.get('content')
        if char is None:
            continue
        if not delta:
            break
        text.write(char)
        if text.getvalue().endswith('\n\n'):
            yield text.getvalue()[:-2]
            text = StringIO()
    yield _rm_pre_text(text.getvalue())


def run(body:str, model="gpt-3.5-turbo"):
    '''询问柚子单句问题
.chat <内容>
多句请使用“柚子聊聊天”截断前文
然后使用“柚子，”开头”'''

    chat_client = Chat(model=model)
    init_chat(chat_client)

    chat_client.messages = [msg2chat({**cache.thismsg(),**{'message':body.lstrip()}})]
    # chats = []
    # while chat_contexts:
    #     message = chat_contexts.pop()
    #     if len(chats)<100:
    #         chats.insert(0, message)
    # if cache.get('debug_chat') and chat_contexts:
    #     print('#被截断的消息有', len(chat_contexts), '条。')
    # chats.append({'role':'assistant','content':''})

    # return chat_client.call().content
    tools = [v.description for v in chat_client.tools.values()]
    # model = model if model is not None else chat_client.model
    model = chat_client.model
    stream = chat_client.req(tools, "auto", model)
    res_msg = add(stream, chat_client)
    tool_calls = res_msg.tool_calls

    while tool_calls:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            try:
                content = chat_client.tools[function_name].call(**json.loads(tool_call.function.arguments))
            except:
                content =f'called: {json.loads(tool_call.function.arguments)}\n\n'+ traceback.format_exc()
            add({
                "role": "tool",
                "name": function_name,
                "content": content,
                "tool_call_id": tool_call.id,
            }, chat_client)
        stream = chat_client.req(tools, "auto", model)
        res_msg = add(stream, chat_client)
        tool_calls = res_msg.tool_calls
