'''gpt3.5'''

import time
import re
from datetime import datetime
from io import StringIO
import traceback
from typing import Any, Callable, Tuple
import requests
from urllib.request import quote, unquote
from termcolor import colored
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import openai
import tiktoken


from main import sendmsg
from main import cache, msg_id, storage, str_tool
from main import settings, getchatstorage, chat_groups
from main import is_msg, is_poke, has_at, find, getlog, msg2chat, chat2msg, getcmd, getgroupname, getname, lunar_time, 小六壬, sendmsg as _sendmsg
from main import cq
from main import CommandManager

import json

from main import LLMCilent, Chat, sum_res, LLMResponse, get_image_base64

from main import memberlist

# from main import hipporag
from main import search_city, get_realtime_weather, get_daily_forecast, get_hourly_forecast
from main import getstorage
import xml.etree.ElementTree as ET

llm_cilent = LLMCilent()

llm_config = storage.get("llm_system", "config")
description_dict = storage.get("llm_system", "description_cache")

# _url_pattern = re.compile(r'https?://[^\?]+\?.*rkey=([^&]+)')
# def _format_key(key):
#     match = _url_pattern.search(key)
#     if match:
#         return match.group(1)
#     return key

# class DescriptionCache:
#     def __init__(self, original_dict):
#         self.original_dict = original_dict

#     def __contains__(self, key):
#         return _format_key(key) in self.original_dict

#     def __getitem__(self, key):
#         return self.original_dict[_format_key(key)]

#     def __setitem__(self, key, value):
#         self.original_dict[_format_key(key)] = value

#     def get(self, key, default=None):
#         return self.original_dict.get(_format_key(key), default)

# description_cache = DescriptionCache(description_dict)
description_cache = description_dict

encoding = tiktoken.encoding_for_model('gpt-4')

def count_tokens(text:str):
    return len(encoding.encode(text))

prices = {
    "gpt-3.5-turbo-ca": (1, 3),
    "gpt-3.5-turbo": (3.5, 10.5),
    "gpt-3.5-turbo-1106": (7, 14),
    "gpt-3.5-turbo-0125": (3.5, 10.5),
    "gpt-3.5-turbo-16k": (21, 28),
    "gpt-4": (210, 420),
    "gpt-4o": (17.5, 70),
    "gpt-4o-2024-05-13": (35, 105),
    "gpt-4o-2024-08-06": (17.5, 70),
    "chatgpt-4o-latest": (35, 105),
    "gpt-4o-mini": (1.05, 4.2),
    "gpt-4-0613": (210, 420),
    "gpt-4-turbo-preview": (70, 210),
    "gpt-4-0125-preview": (70, 210),
    "gpt-4-1106-preview": (70, 210),
    "gpt-4-vision-preview": (70, 210),
    "gpt-4-turbo": (70, 210),
    "gpt-4-turbo-2024-04-09": (70, 210),
    "gpt-4-ca": (120, 240),
    "gpt-4-turbo-ca": (40, 120),
    "gpt-4o-ca": (10, 40),
    "gpt-3.5-turbo-instruct": (10.5, 14),
    "claude-3-5-sonnet-20240620": (15, 75),
    "claude-3-5-sonnet-20241022": (15, 75),
    "claude-3-5-haiku-20241022": (5, 25),
    "deepseek-reasoner": (4,16),
    "deepseek-chat": (1,2),
    "deepseek-ai/DeepSeek-R1":(4,16),
    "deepseek-ai/DeepSeek-V3": (1,2),
    "Pro/deepseek-ai/DeepSeek-R1": (4,16),
    "Pro/deepseek-ai/DeepSeek-V3": (1,8),
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B": (0,0),
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": (0,0),
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": (0,0),
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B": (0, 0.7),
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B": (0, 1.26),
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": (0, 4.13)
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

def get_attr(provider:str, model:str):
    return llm_config['providers'].get(provider, {}).get('models',{}).get(model,{})

def get_prices(provider:str, model:str):
    attr = get_attr(provider, model)
    prompt_price = attr.get('prompt_price', 0)
    completion_price = attr.get('completion_price', 0)
    return (prompt_price, completion_price)

cost_lock = threading.Lock()
def inc_call_tokens_cost(provider, model, tokens: tuple[int, int]):
    with cost_lock:
        (prompt_tokens, completion_tokens) = tokens
        (prompt_price, completion_price) = get_prices(provider, model)
        price = (prompt_tokens*prompt_price + completion_tokens*completion_price)/1_000_000
        get_caller()[1] += price

def inc_call_token_cost(provider, model, type:int, token:int):
    prices = get_prices(provider, model)
    price = token*prices[type]/1_000_000
    get_caller()[1] += price

def inc_call_text_cost(provider, model, type:int, text:str):
    token = count_tokens(text)
    inc_call_token_cost(provider, model, type, token)

def inc_call_image_cost(size:str, quality:str):
    price = 0.28
    if size != "1024x1024":
        price += 0.28
    if quality == 'hd':
        price += 0.28
    get_caller()[1] += price

def group_state(s=None):
    group_id = cache.thismsg().get('group_id')
    if group_id:
        return {'role':'system','content':f'当前所在群聊:{getgroupname(group_id)}({group_id})'}
    else:
        user_id = cache.thismsg().get('user_id')
        return {'role':'system','content':f'当前在私聊:{getname(user_id)}({user_id})'}

































#-------------------------------------------------------------------------------------------------------------

all_func_names = set(vars().keys())

def get_time():
    '''
    获取当前时间
    '''
    return f'现在是{time.strftime("%Y年%m月%d日%H时%M分%S秒")}'

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
    若用python读取和编辑`data`字典，其中的数据会被持久化保存

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
    处于不可用状态

    @param
    prompt: Description text used to create the image
    size: Picture size
        enum: ["1024x1024", "1024x1792", "1792x1024"]
    quality: Image quality
        enum: ["standard", "hd"]
    '''
    # inc_call_image_cost(size, quality)
    # picCQ = url2cq(chat_client.create_image(prompt, size, quality))
    # sendmsg(picCQ)
    # return picCQ

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

def get_user_data(user_id:int):
    '''
    查询用户数据，返回用户的数据字典，可以用于查询用户的经纬度或者城市id等

    @param
    user_id: 用户的qq号
    '''
    return str(getstorage(user_id))

def set_user_data(user_id:int,key:str,value:str):
    '''
    编辑用户的数据字典，可以用于设置用户的城市id等

    @param
    user_id: 用户的qq号
    key: 需要编辑的键
    value: 需要设置的值，接受python表达式，如果是del，则删除这个键
    '''
    try:
        sto = getstorage(user_id)
        if value=='del':
            del sto[key]
        else:
            sto[key]=eval(value)
        return 'done'
    except Exception as e:
        return str(e)

def assign_tasks(prompt: str, tasks: str, tools: str, model: str = "deepseek-v3", max_workers: int = 5):
    '''
    使用多线程并发执行，用于分派子任务给其它AI，返回一个包含结果的列表，每个元素是一个元组 (task, result_text)
    当任务可以分解为上下文无关的独立子任务时，分派给其它AI模型，可以节省上下文和提升效率
    当任务非常繁杂时，不应该自己管理所有AI，可以发给子AI让子AI再发给子子AI，分而治之，并确保任务所需上下文准确传达。那么每层AI只需要总结少量几个的AI总结
    当你是父AI时，请明确指定子AI使用的模型
    当你是子AI时，并发数必须为1，否则很容易并发数过多

    @param
    model: 调用的大语言模型
    prompt: 给每个子AI的统一提示词，如 prompt="搜索这个我的世界mod并总结内容:"
    tasks: 附加在统一提示词后面的内容，每行对应一个任务，如 tasks="acedium\\nalexs caves delight\\nArgentina s delight"
    tools: 分派给所有AI使用的工具，每行一个名称，不能重复，必须是你已有的工具，如 "search_mc_mod\\ncheck_mod"
    max_workers: 最大并发线程数，默认为5。
    '''

    # 1. 将任务和工具预处理成列表，过滤掉空行
    task_list = [t for t in tasks.split('\n') if t.strip()]
    tool_list = [t for t in tools.split('\n') if t.strip()]

    msg = cache.thismsg()

    # 2. 定义 Worker 函数（处理单个任务的逻辑）
    #    我们把它定义在主函数内部，这样它可以方便地访问 prompt, tool_list 等变量
    def run_single_task(task: str) -> tuple[str, str]:
        """为单个任务执行LLM调用并返回结果"""
        task_result = ""

        def handle_LLMResponse(chunk: LLMResponse):
            nonlocal task_result
            cache.thismsg(msg)
            # print("handle",chunk)
            if chunk.role == 'assistant' and chunk.content:
                # 确保内容是字符串
                if isinstance(chunk.content, list):
                    # 如果是列表，提取文本部分
                    text_content = ""
                    for item in chunk.content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_content += item["text"]
                        elif isinstance(item, str):
                            text_content += item
                    task_result += text_content
                else:
                    task_result += str(chunk.content)

            if chunk.total_tokens:
                # 调用线程安全的费用更新函数
                inc_call_tokens_cost(
                    chat_client.provider,
                    chat_client.model,
                    (chunk.prompt_tokens, chunk.completion_tokens),
                )

        try:
            # 每个线程创建自己独立的 ChatClient 实例，这是非常重要的，可以避免状态混淆
            chat_client = Chat(provider='openai', model=model, chat_client=llm_cilent)

            for tool_name in tool_list:
                if tool_name in all_funcs:
                    chat_client.add_tool(all_funcs[tool_name])

            chat_client.set_messages([f"{prompt}\n{task}"])

            print(f"线程 {threading.get_ident()}: 开始处理任务 '{task}'")

            chat_client.chat(
                recall_func=handle_LLMResponse
            )

            print(f"线程 {threading.get_ident()}: 完成任务 '{task}'")
            return (task, task_result.strip())

        except Exception as e:
            print(f"线程 {threading.get_ident()}: 任务 '{task}' 发生错误: {e}")
            return (task, f"ERROR: {e}")


    # 3. 创建并使用线程池来并发执行任务
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 使用 executor.submit 提交所有任务
        # future_to_task 是一个字典，用于在任务完成时找回原始任务内容
        future_to_task = {executor.submit(run_single_task, task): task for task in task_list}

        # 使用 as_completed 来获取已完成任务的结果，哪个先完成就先处理哪个
        for future in as_completed(future_to_task):
            task_name = future_to_task[future]
            try:
                # .result() 会获取 worker 函数的返回值
                result_tuple = future.result()
                results.append(result_tuple)
            except Exception as exc:
                # 如果 worker 函数本身抛出未被捕获的异常，这里会捕捉到
                print(f"任务 '{task_name}' 在执行期间生成了异常: {exc}")
                results.append((task_name, f"EXECUTION FAILED: {exc}"))

    # 可以选择对结果进行排序，使其与输入任务的顺序一致
    # 如果不需要保持顺序，直接返回 results 即可
    ordered_results = sorted(results, key=lambda x: task_list.index(x[0]))

    return repr(ordered_results)

def search_mc_mod(name:str):
    '''
    通过mcmod搜索我的世界mod

    @param
    name: 关键字，最好是mod名字，宜少不宜多，不接受带版本和后缀的字符串
    '''
    from bs4 import BeautifulSoup
    response = requests.get(f'https://search.mcmod.cn/s?key={name}')
    soup = BeautifulSoup(response.text, 'html.parser')

    results = []
    elements = soup.find_all(class_='search-result-list')

    for element in elements:
        results.append(element.get_text().strip())

    full_result = '\n'.join(results)
    # 截断结果，只返回前1000字符以避免过长
    if len(full_result) > 1000:
        full_result = full_result[:1000] + "..."
    return full_result

def check_mod(id:int):
    '''
    通过mcmod链接id查询我的世界mod

    @param
    id: 查询 f"https://www.mcmod.cn/class/{id}.html" 中的信息
    '''
    from bs4 import BeautifulSoup
    response = requests.get(f'https://www.mcmod.cn/class/{id}.html')
    soup = BeautifulSoup(response.text, 'html.parser')

    results = []

    elements = soup.find_all(class_='text-area')

    for element in elements:
        results.append(element.get_text().strip())

    return '\n'.join(results)


all_funcs = {name:func for name, func in vars().items() if callable(func) and name not in all_func_names}
# def rag_search(queries:str, num_to_retrieve:int=1):
#     '''
#     主动回忆

#     @param
#     queries: 查询的问题，用英文逗号分隔
#     num_to_retrieve: 每个查询返回的结果数量
#     '''
#     # 将今天、昨天、明天替换为对应时间
#     queries = queries.replace('今天', time.strftime('%Y年%m月%d日')).replace('昨天', time.strftime('%Y年%m月%d日', time.localtime(time.time() - 86400))).replace('明天', time.strftime('%Y年%m月%d日', time.localtime(time.time() + 86400)))
#     return hipporag.retrieve(queries.split(','), num_to_retrieve)

#---------------------------------------------------------------------------------------------------------------































prompts = storage.get('llm_system', 'prompts')


def get_prompt() -> list:
    data = getchatstorage()
    name = data.get('prompt') #可能是name索引或者list
    if not name:
        return settings
    elif isinstance(name, str) and name in prompts:
        name = prompts[name]

    if isinstance(name, list):
        return name
    else:
        return []

max_token = storage.get('llm_system', 'config').get('max_token', 4000)
max_msg = storage.get('llm_system', 'config').get('max_msg', 200)

def get_msgs(max_token=max_token, return_token=False):
    in_group = cache.thismsg().get('group_id')

    chat_logs = []
    for msg in getlog()[:max_msg]:
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
                    sum_token += part.get('token_cost',0)
                else:
                    print(f"chat error: 消息中有text和image_url之外的对象: {part}")
        else:
            print(f"chat error: 消息列表中有非列表非字符串的对象: {repr(content)}")

        if sum_token > max_token:
            break

        messages.insert(0, chat_msg)
        # if messages and chat_msg['role'] == messages[0]['role']:
        #     #TODO 默认全是字符串
        #     messages[0]['content'] = f'{content}\n\n{messages[0]["content"]}'
        # else:
        #     messages.insert(0, chat_msg)

    if messages[-1]['role'] == 'assistant':
        messages[-1]['prefix'] = True
        # messages.append({'role':'user','content':f'---\nsystem\n---\n这是为了防止报错而添加的分隔线'})

    if return_token:
        return messages, sum_token
    return messages

def init_chat(chat_client:Chat, messages=[]):
    '''
    添加工具，设定
    '''
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
    # chat_client.add_tool(create_image)
    # chat_client.add_tool(chat_client.read_image)
    # chat_client.add_tool(url2cq)
    # chat_client.add_tool(muti_reply)
    # chat_client.add_tool(baidu_encyclopedia)

    chat_client.add_tool(search_city)
    chat_client.add_tool(get_realtime_weather)
    chat_client.add_tool(get_daily_forecast)
    chat_client.add_tool(get_hourly_forecast)
    chat_client.add_tool(get_user_data)
    chat_client.add_tool(set_user_data)
    # chat_client.add_tool(rag_search)
    # chat.add(chat.req())
    chat_client.add_tool(assign_tasks)
    chat_client.add_tool(search_mc_mod)
    chat_client.add_tool(check_mod)

    # last_data = None
    # def show_data(s):
    #     data = getcmd('py').data
    #     global last_data
    #     result = '`data`内的键及类型: {'+', '.join([f"`{k}`: {type(v)}" for k, v in data.items()])+'}' if data!=last_data else None
    #     last_data = data.copy()
    #     return result

    # data = getchatstorage()

    prompts['base'] = [
#         {'role':'system', 'content':'''## 注意事项
# - 你的昵称: 柚子
# - 你的QQ号：0。at格式:"[CQ:at,qq=qq号]"(仅在群聊下有效)；reply格式:"[CQ:reply,id=message_id]"
# - 你的回复需要严格按照以下XML格式输出，如果需要更新记忆，采用先删除，后添加的方式：
# <message>
#   <content>
#     <text>你的回复内容</text>
#   </content>
# </message>

# - 聊天中可能不会有明显的问题，扮演好角色即可
# - 如无特殊要求，请用中文回复'''},
        {'role':'system', 'content':f'''## 注意事项
- 你的昵称: 柚子
- 你的QQ号：{cache.qq}。at格式:"[CQ:at,qq=qq号]"(仅在群聊下有效)；reply格式:"[CQ:reply,id=message_id]"
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复'''}
    ]

    chat_client.set_messages([
            *get_prompt(),
            *prompts.get('base'),
            group_state,
            *messages
            ])

    data = getchatstorage()
    chat_client.do_process_image = data.get('image', False)
    # if 'split' not in data: # 设置默认值
    #     data['split'] = True
    # chat_client.split = data['split'] #决定是否划分发送


cm = CommandManager()

# @cm.register('split')
# def _()->str:
#     '''
#     切换是否划分发送消息
#     '''
#     data = getchatstorage()
#     data['split'] = not data.get('split')
#     return f"split: {data['split']}"

def format_price(model: str, attr: dict) -> str:
    return f"{model}\n    {attr.get('prompt_price', '-')} {attr.get('completion_price', '-')} { '👀' if attr.get('vision') else ''} { '⚙️' if attr.get('function_calling') else ''}"

@cm.register('provider')
def get_provider() -> str:
    '''
    查看当前供应商
    '''
    return getchatstorage().get('provider', llm_config.get('default_provider', 'openai'))

@cm.register('model')
def get_model() -> str:
    '''
    查看当前模型
    '''
    return getchatstorage().get('model', llm_config.get('default_model', 'gpt-4o-mini'))

@cm.register('models')
def list_models() -> str:
    '''
    列出所有模型的价格
    '''
    models = llm_config.get('providers', {}).get(get_provider(), {}).get('models', {})
    return "\n".join(["模型 输入价格 输出价格 (单位: 元/(1m token)) 视觉识别 函数调用"]+[format_price(model, attr) for model, attr in models.items()])

@cm.register('model <model:str>')
def list_specific_model(model: str) -> str:
    '''
    列出特定模型的价格
    '''
    model_attr = llm_config.get('providers', {}).get(get_provider(), {}).get('models', {}).get(model)
    if model_attr:
        return "模型 输入价格 输出价格 (单位: 元/(1k token)) 视觉识别 函数调用\n"+format_price(model, model_attr)
    else:
        return f"未找到模型: {model}"

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
    使用模型
    '''
    getchatstorage()['model'] = model
    return f'模型设置为 {model}'

@cm.register('prompt')
def _()->list:
    '''
    查看当前提示词
    '''
    name = getchatstorage().get('prompt')
    if name is None:
        return f'{settings}\n(默认)'
    elif isinstance(name, str):
        return f'{prompts[name]}\n({name})'
    else:
        return f'{name}'

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

@cm.register('setting')
def _()->str:
    '''
    列出当前所有设定名字
    '''
    return '\n'.join(list(prompts.keys()))

@cm.register('setting <name:str>')
def _(name:str)->list|str:
    '''
    查找设定
    '''
    prompt = prompts.get(name)
    if prompt:
        return prompt
    else:
        return '未找到设定，你可能需要先创建设定'

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
    if not prompts.get(name) is None:
        getchatstorage()['prompt'] = name
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

@cm.register('image')
def _()->str:
    '''
    切换是否读取图片
    '''
    data = getchatstorage()
    data['image'] = not data.get('image')
    return f"image: {data['image']}"


# def get_balance(base_url, api_key):
#     url = f'{base_url}/user/balance'
#     headers = {
#         'Accept': 'application/json',
#         'Authorization': f'Bearer {api_key}'
#     }

#     response = requests.get(url, headers=headers)

#     if response.status_code == 200:
#         return response.json()
#     else:
#         return f"错误: {response.status_code}, {response.text}"

# @cm.register('balance <model:str>')
# def _(model:str)->str:
#     '''
#     查询余额
#     '''
#     import os
#     if model.startswith('deepseek'):
#         return get_balance(os.getenv('DEEPSEEK_BASE_URL'), os.getenv('DEEPSEEK_API_KEY'))
#     else:
#         return '暂不支持查询'


def cond() -> Callable | bool:
    '''
    被links引用
    作为条件调用，返回非空值(data)以触发结果(call(data))
    '''
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
    '''
    被links引用
    data来自cond函数的返回值
    '''
    # 如果是命令则执行
    if callable(data):
        return '#'+cq.escape(str(data()))

    return chat()

# role_to_color = {
#     "system": "red",
#     "user": "green",
#     "think": "yellow",
#     "assistant": "blue",
#     "tool": "magenta",
# }
# def show_args(args):
#     return ', '.join([f'{k}={repr(v)}' for k, v in args.items()])
# def show_tool_calls(tool_calls):
#     return ''.join(map(lambda s:f'\n    {s["function"]["name"]}({show_args(json.loads(s["function"]["arguments"]))})', tool_calls))
# def split_string_with_code_blocks(text:str):
#     result = []
#     count = 0

#     for part in text.split('\n\n'):
#         last_is_code = count%2==1
#         count += part.startswith('```')+part.count('\n```')
#         if last_is_code and count%2==1:
#             result[-1] += '\n\n'+part
#         else:
#             result.append(part)

#     return result

# def pprint(message:dict | ChatCompletionMessage | MessageStream | str, model:str, split=True):
#     '''
#     打印 dict, 普通消息, 或者流式消息, 然后返回
#     流式消息会转换为普通消息
#     '''
#     if isinstance(message, str):
#         print(colored(f"system: {message}", "red"))
#         _sendmsg('#error: '+message)
#         return ChatCompletionMessage(role='system', content=message)
#     elif isinstance(message, MessageStream):
#         role = message.role
#         tool_calls = message.tool_calls
#         if message.tool_calls:
#             print(colored(f"assistant called: {tool_calls[0].function.name} ", "yellow"),end='', flush=True)
#             text:str = ''
#             for delta in message:
#                 if isinstance(delta, Tuple):
#                     inc_call_tokens_cost(model, delta)
#                 else:
#                     text += delta
#                     print(colored(delta, "yellow"),end='', flush=True)
#         else:
#             print(colored(f"assistant: ", role_to_color[role]),end='', flush=True)
#             sum_text:str = ''
#             text:str = ''
#             thinking = False
#             for delta in message:
#                 if isinstance(delta, Tuple):
#                     inc_call_tokens_cost(model, delta)
#                 elif isinstance(delta, dict):
#                     if reasoning_content:=delta.get('reasoning_content'):
#                         print(colored(reasoning_content, role_to_color['think']),end='', flush=True)
#                     elif content:=delta.get('content'):
#                         if '<think>' in content:
#                             thinking = True
#                             pre_content, content = content.split('<think>')
#                             print(colored(pre_content, role_to_color[role]),end='', flush=True)
#                         if thinking:
#                             if '</think>' in content:
#                                 thinking = False
#                                 reasoning_content, content = content.split('</think>')
#                             else:
#                                 reasoning_content, content = content, ''
#                             print(colored(reasoning_content, role_to_color['think']),end='', flush=True)
#                         print(colored(content, role_to_color[role]),end='', flush=True)
#                         text += content
#                         sum_text += content
#                         if split:
#                             *parts, text = split_string_with_code_blocks(text)
#                             for part in parts:
#                                 _sendmsg(part)
#                     else:
#                         print('这里不应该运行到，因为若没有reasoning_content和content应该停止循环')
#                 else:
#                     print('这里不应该运行到，因为只返回了这两个类型的值')
#             if text:
#                 _sendmsg(text)
#         print('\n')
#         return message.msg
#     else:
#         raise ValueError('没想好怎么处理')
#         # if (isinstance(message,ChatCompletionMessage)):
#         #     msg = message.dict()
#         # else:
#         #     msg = message
#         # role = msg.get('role')
#         # tool_calls = msg.get('tool_calls')
#         # content = msg.get('content')
#         # name = msg.get('name')
#         # if role == "system":
#         #     print(colored(f"system: {content}\n", role_to_color[role]))
#         # elif role == "user":
#         #     pass
#         #     # print(colored(f"user: {content}\n", role_to_color[role]))
#         # elif role == "assistant" and tool_calls:
#         #     print(colored(f"assistant called: {show_tool_calls(tool_calls)}\n", "yellow"))
#         #     #TODO 没想好应该怎么计算
#         # elif role == "assistant" and not tool_calls:
#         #     print(colored(f"assistant: {content}\n", role_to_color[role]))
#         #     inc_call_text_cost(model, 1, content)
#         # elif role == "tool":
#         #     print(colored(f"function ({name}): {content}\n", role_to_color[role]))
#         # else:
#         #     print('else:',msg)
#         return message

# def add(msg, chat_client:Chat):
#     """
#     Print, transform and add a message to the chat history, then return the transformed message
#     流式消息会被转换为普通消息

#     :param msg: The message to be added.
#     :return: The added message.
#     """
#     msg = pprint(msg, chat_client.model, chat_client.split)
#     chat_client.messages.append(msg)
#     return msg


def get_handler(chat_client:Chat):
    def handle_LLMResponse(chunk: LLMResponse):
        if chunk.role == 'assistant' and chunk.content:
            text = chunk.content
            # # 使用正则表达式提取内容
            # text_match = re.search(r'<text>(.*?)</text>', chunk.content, re.DOTALL)
            # text = text_match.group(1) if text_match else chunk.content

            # # 提取所有memory标签内容
            # memories = re.findall(r'<memory>(.*?)</memory>', chunk.content, re.DOTALL)

            # # 提取out_of_date内容
            # out_of_date_match = re.search(r'<out_of_date>(.*?)</out_of_date>', chunk.content, re.DOTALL)
            # out_of_date = out_of_date_match.group(1) if out_of_date_match else None

            # 发送回复内容
            _sendmsg(text)

            # # 如果有记忆点，存储到 RAG 系统
            # if memories:
            #     hipporag.index(docs=[memory+f'\n记录时间: {time.strftime("%Y年%m月%d日 %H时")} 于 {location}' for memory in memories if memory.strip()])

            # # 如果有需要删除的记忆，从 RAG 系统中删除
            # if out_of_date:
            #     try:
            #         indices = [int(idx.strip()) for idx in out_of_date.split(',')]
            #         if results and indices:
            #             docs_to_delete = [results[idx-1] for idx in indices if 0 < idx <= len(results)]
            #             if docs_to_delete:
            #                 hipporag.delete(docs_to_delete)
            #                 print(f"已删除 {len(docs_to_delete)} 条过期记忆")
            #     except Exception as e:
            #         print(f"删除记忆失败: {e}")

        if chunk.total_tokens:
            inc_call_tokens_cost(
                chat_client.provider,
                chat_client.model,
                (chunk.prompt_tokens, chunk.completion_tokens),
            )
    return handle_LLMResponse

def chat(model=None):
    chat_client = Chat(provider=get_provider(), model=model or get_model(), chat_client=llm_cilent)

    # msg = cache.thismsg()
    # if 'group_id' in msg:
    #     location = f'群聊 {msg.get("group_id")}'
    # else:
    #     location = f'私聊 {msg.get("user_id")}'

    # # 获取最近3条消息并合并为查询字符串
    messages = get_msgs()
    # query = "\n".join([
    #     " ".join([
    #         part.get("text", "")
    #         for part in msg["content"]
    #         if isinstance(part, dict) and "text" in part
    #     ]) if isinstance(msg, dict) else str(msg) for msg in messages[-3:]
    # ])

    # # 进行 RAG 查询
    # # print(query)
    # results = []
    # try:
    #     print('开始查询')
    #     results = hipporag.retrieve([query], num_to_retrieve=5)[0].docs
    # except Exception as e:
    #     print(f'查询中遇到错误: {traceback.format_exc()}')
    # # print(results)
    # if results:
    #     print('查询成功')
    #     memory_results = []
    #     for i, doc in enumerate(results, 1):
    #         memory_results.append(f"{i}. {doc}")
    #     memory_text = "\n".join(memory_results)
    #     messages.insert(0, {'role': 'system', 'content': f"找到以下相关记忆:\n{memory_text}"})
    # else:
    #     print('查询失败')

    init_chat(chat_client, messages)

    chat_client.print_messages()
    chat_client.chat(
        recall_func=get_handler(chat_client),
        url_to_base64_func=get_image_base64,
        description_cache=description_cache,
    )

    # 增加计费
    # inc_call_token_cost(model, 0, sum_token)

    # chats = []
    # while chat_contexts:
    #     message = chat_contexts.pop()
    #     if len(chats)<100:
    #         chats.insert(0, message)
    # if cache.get('debug_chat') and chat_contexts:
    #     print('#被截断的消息有', len(chat_contexts), '条。')
    # chats.append({'role':'assistant','content':''})

    # return chat_client.call().content
    # tools = [v.description for v in chat_client.tools.values()]
    # model = model if model is not None else chat_client.model
    # try:
    #     stream = chat_client.req(tools, "auto", model)
    # except StopIteration:
    #     _sendmsg('# 模型返回了空消息')
    #     return
    # res_msg = add(stream, chat_client)
    # tool_calls = res_msg.tool_calls
    # while tool_calls:
    #     for tool_call in tool_calls:
    #         function_name = tool_call.function.name
    #         try:
    #             content = chat_client.tools[function_name].call(**json.loads(tool_call.function.arguments))
    #         except:
    #             content =f'called: {json.loads(tool_call.function.arguments)}\n\n'+ traceback.format_exc()
    #         add({
    #             "role": "tool",
    #             "name": function_name,
    #             "content": content,
    #             "tool_call_id": tool_call.id,
    #         }, chat_client)
    #     try:
    #         stream = chat_client.req(tools, "auto", model)
    #     except StopIteration:
    #         _sendmsg('# 模型返回了空消息')
    #         return
    #     res_msg = add(stream, chat_client)
    #     tool_calls = res_msg.tool_calls

# def _rm_pre_text(text:str):
#     name = cache.nicknames[0]
#     pre = f'[{name}]({cache.qq}): '
#     if text.startswith(pre):
#         return text.lstrip(pre)
#     return text

# def process_res(res):
#     text = StringIO()
#     for chunk in res:
#         delta = chunk['choices'][0]['delta']
#         char = delta.get('content')
#         if char is None:
#             continue
#         if not delta:
#             break
#         text.write(char)
#         if text.getvalue().endswith('\n\n'):
#             yield text.getvalue()[:-2]
#             text = StringIO()
#     yield _rm_pre_text(text.getvalue())


def run(body:str, model="gpt-3.5-turbo"):
    '''询问柚子单句问题
.chat <内容>
多句请使用"柚子聊聊天"截断前文
然后使用"柚子，"开头"'''


    chat_client = Chat(provider=get_provider(),model=model or get_model(),chat_client=llm_cilent)
    init_chat(chat_client, [
        {
            'role': 'assistant',
            'content': body
        }
    ])

    def handle_LLMResponse(chunk: LLMResponse):
        if chunk.role == 'assistant' and chunk.content:
            _sendmsg(chunk.content)
        if chunk.total_tokens:
            inc_call_tokens_cost(
                chat_client.provider,
                chat_client.model,
                (
                    chunk.prompt_tokens,
                    chunk.completion_tokens,
                ),
            )

    chat_client.print_messages()
    chat_client.chat(
        recall_func=handle_LLMResponse,
        url_to_base64_func=get_image_base64,
        description_cache=description_cache,
    )


    # chat_client = Chat(model=model)
    # init_chat(chat_client)

    # chat_client.messages = [msg2chat({**cache.thismsg(),**{'message':body.lstrip()}})]
    # # chats = []
    # # while chat_contexts:
    # #     message = chat_contexts.pop()
    # #     if len(chats)<100:
    # #         chats.insert(0, message)
    # # if cache.get('debug_chat') and chat_contexts:
    # #     print('#被截断的消息有', len(chat_contexts), '条。')
    # # chats.append({'role':'assistant','content':''})

    # # return chat_client.call().content
    # tools = [v.description for v in chat_client.tools.values()]
    # # model = model if model is not None else chat_client.model
    # model = chat_client.model
    # stream = chat_client.req(tools, "auto", model)
    # res_msg = add(stream, chat_client)
    # tool_calls = res_msg.tool_calls

    # while tool_calls:
    #     for tool_call in tool_calls:
    #         function_name = tool_call.function.name
    #         try:
    #             content = chat_client.tools[function_name].call(**json.loads(tool_call.function.arguments))
    #         except:
    #             content =f'called: {json.loads(tool_call.function.arguments)}\n\n'+ traceback.format_exc()
    #         add({
    #             "role": "tool",
    #             "name": function_name,
    #             "content": content,
    #             "tool_call_id": tool_call.id,
    #         }, chat_client)
    #     stream = chat_client.req(tools, "auto", model)
    #     res_msg = add(stream, chat_client)
    #     tool_calls = res_msg.tool_calls
