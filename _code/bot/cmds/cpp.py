'''cpp!'''

import os
import subprocess
from main import cache, cq, file, sendmsg

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
    return compile_and_run_cpp()


def ensure():
    '''保证有cpp'''
    def err(name, info):
        return name+' 错误:\n'+info
    s =os.popen('g++ --version').read()
    if 'g++ (GCC)' not in s:
        return False, err('node', s)
    return True, None

def compile_and_run_cpp():
    # 尝试编译 C++ 程序
    compile_process = subprocess.run(['g++', 'data/tmp.cpp', '-o', 'data/tmp'], capture_output=True, text=True)
    
    # 检查编译是否成功
    if compile_process.returncode != 0:
        return f'编译失败:\n{compile_process.stderr}'
    
    # 编译成功，运行程序
    run_process = subprocess.run(['data/tmp'], capture_output=True, text=True)
    

    # 检查程序运行是否成功
    sendmsg(run_process.stdout)
    if run_process.returncode != 0:
        sendmsg(run_process.stderr)
    
    return f'程序已退出，返回值 {run_process.returncode}'
