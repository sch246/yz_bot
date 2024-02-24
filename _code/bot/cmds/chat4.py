'''gpt4'''

from .chat import chat as _chat, run as _run

def chat():
    return _chat(model="gpt-4-1106-preview")

def run(body:str, model="gpt-4-1106-preview"):
    '''询问柚子单句问题
.chat4 <内容>
多句请使用“柚子聊聊天”截断前文
然后使用“神奇柚子，”开头”'''
    return _run(body, model)
