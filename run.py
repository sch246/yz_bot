'''启动bot'''

import subprocess
import sys
import time

if any(c in sys.argv[1:] for c in ['-h','--help','help']):
    print('''接受的参数:
    -h              显示本提示
    -a              当出现异常时自动重启
    --auto_reboot   同上
    -l              仅记录log
    --log_only      同上
    debug           debug模式(并没有什么区别)''')
    exit()

AUTO_REBOOT = any(c in sys.argv[1:] for c in ['--auto_reboot', '-a'])
while True:
    out = subprocess.run([sys.executable,'./_code/main.py',*sys.argv[1:]], check=False)
    print('已退出，返回码为',out.returncode)
    if out.returncode in [233,-6]: # 当python多线程写入中强制关闭时，返回是-6
        print('重启中...')
        continue
    if AUTO_REBOOT and out.returncode!=0:
        print('自动重启中...')
        time.sleep(1)
        continue
    break
