'''Haskell!'''
from main import repl
from main import cache, cq, send, to_thread

ghci_path = "/root/.ghcup/bin/ghci"
ghci_repl = repl.Repl([ghci_path])
ghci_sign = "ghci>"

def run(body: str):
    '''运行Haskell代码
    格式:
    .hs <Code>'''
    msg = cache.thismsg()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.hs'):
            return '权限不足(一定消息内将不再提醒)'

    flag, value = repl.ensure([ghci_path, '--version'])
    if not flag:
        return value

    if body == ':quit':
        ghci_repl.stop()
        return "GHCi 已关闭"

    def sendmsg(text: str):
        text = text.replace(f"{ghci_sign} ", "")
        if not text:
            text = "结果为空"
        send(text, **msg)

    to_thread(ghci_repl.run_code)(body, sendmsg, ghci_sign, timeout=30)

