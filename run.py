'''启动bot'''

import subprocess,sys
while True:
    out = subprocess.run([sys.executable,'./_code/main.py',*sys.argv[1:]])
    print('已退出，返回码为',out.returncode)
    if out.returncode==2333:
        print('重启中...')
        continue
    break