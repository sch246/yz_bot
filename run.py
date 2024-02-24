'''启动bot'''

import subprocess
import sys
import time
import click

@click.command()
@click.option('-l', '--log-only', 'log_only', is_flag=True, help='是否仅记录log')
@click.option('-a', '--auto-reboot', 'auto_reboot', is_flag=True, help='自动重启')
@click.option('-q', '--qq', 'qq', type=int, default=5700, help='发送端口, go-cqhttp 的监听端口')
@click.option('-p', '--port', 'port', type=int, default=5701, help='监听端口')
def run(log_only, auto_reboot, qq, port):
    args = []
    if log_only:
        args.append('-a')
    args.extend(['-q',str(qq)])
    args.extend(['-p',str(port)])
    try:
        while True:
            out = subprocess.run([sys.executable,'./_code/main.py',*args], check=False)
            print('已退出，返回码为',out.returncode)
            if out.returncode in [233,-6]: # 当python多线程写入中强制关闭时，返回是-6
                print('重启中...')
                continue
            if auto_reboot and out.returncode!=0:
                print('自动重启中...')
                time.sleep(1)
                continue
            break
    except KeyboardInterrupt:
        exit()

if __name__=='__main__':
    run()
