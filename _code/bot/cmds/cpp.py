'''cpp!'''

import os
import subprocess
import select
from main import cache, cq, file, is_msg, send, run_process

def run(body:str):
    '''运行cpp代码，仅在linux上有效
格式:
.cpp <Code>'''
    msg = cache.thismsg()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.py'):
            return '权限不足(一定消息内将不再提醒)'

    flag, value = ensure()
    if not flag:
        return value

    file.write('data/tmp.cpp',body)
    yield from compile_and_run_cpp()


def ensure():
    '''保证有cpp'''
    def err(name, info):
        return name+' 错误:\n'+info
    s =os.popen('g++ --version').read()
    if 'g++' not in s:
        return False, err('g++', s)
    return True, None

def compile_and_run_cpp():
    # 尝试编译 C++ 程序
    compile_process = subprocess.run(['g++', 'data/tmp.cpp', '-o', 'data/tmp'], capture_output=True, text=True)

    # 检查编译是否成功
    if compile_process.returncode != 0:
        return f'编译失败:\n{compile_process.stderr}'

    # 编译成功，运行程序
    yield from run_process(['data/tmp'], lambda text: send(text, **cache.thismsg()))
