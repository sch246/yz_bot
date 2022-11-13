'''启动bot'''

import subprocess,sys

auto_reboot = 'auto_reboot' in sys.argv[1:]
while True:
    out = subprocess.run([sys.executable,'./_code/main.py',*sys.argv[1:]])
    print('已退出，返回码为',out.returncode)
    if out.returncode in [233,-6]: # 当python多线程写入中强制关闭时，返回是-6
        print('重启中...')
        continue
    if auto_reboot and out.returncode!=0:
        print('自动重启中...')
        continue
    break