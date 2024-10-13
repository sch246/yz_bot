'''JavaScript!'''
from main import repl
from main import cache, cq, send, to_thread

node_repl = repl.Repl(['node', '-i'])
node_sign = ">"

def run(body: str):
    '''运行js代码
    格式:
    .js <Code>'''
    msg = cache.thismsg()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.js'):
            return '权限不足(一定消息内将不再提醒)'

    flag, value = repl.ensure(['node', '-v'])
    if not flag:
        return value

    if body==':bye':
        node_repl.stop()
        return "已关闭"

    def sendmsg(text: str):
        text = text.replace(f"{node_sign} ", "")
        if not text:
            text = "结果为空"
        send(text, **msg)

    to_thread(node_repl.run_code)(body, sendmsg, node_sign, timeout=30)

