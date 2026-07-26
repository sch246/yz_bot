'''启动bot'''

import subprocess
import sys
import time
import signal
import click

@click.command()
@click.option('-l', '--log-only', 'log_only', is_flag=True, help='是否仅记录log')
@click.option('-d', '--debug', 'debug', is_flag=True, help='启用debug')
@click.option('-a', '--auto-reboot', 'auto_reboot', is_flag=True, help='自动重启')
@click.option('-q', '--qq', 'qq', type=int, default=5700, help='发送端口, go-cqhttp 的监听端口')
@click.option('-p', '--port', 'port', type=int, default=5701, help='监听端口')
def run(log_only, debug, auto_reboot, qq, port):
    args = []
    if log_only:
        args.append('-l')
    if debug:
        args.append('-d')
    if auto_reboot:
        args.append('-a')
    args.extend(['-q',str(qq)])
    args.extend(['-p',str(port)])
    try:
        while True:
            process = subprocess.Popen(
                [sys.executable, './_code/main.py', *args]
            )

            # 终端可能已把 SIGINT 发给同一进程组里的子进程；父进程只补充
            # 转发第一次，main.py 会忽略保存期间到达的重复 SIGINT。
            sigint_forwarded = False
            def _forward_sigint(sig, frame):
                nonlocal sigint_forwarded
                if sigint_forwarded:
                    return
                sigint_forwarded = True
                if process.poll() is None:
                    try:
                        process.send_signal(sig)
                    except ProcessLookupError:
                        pass

            original_sigint = signal.signal(signal.SIGINT, _forward_sigint)
            try:
                retcode = process.wait()
            finally:
                signal.signal(signal.SIGINT, original_sigint)

            print('已退出，返回码为', retcode)
            if retcode in [233, -6]:  # 当python多线程写入中强制关闭时，返回是-6
                print('重启中...')
                continue
            if auto_reboot and retcode != 0:
                print('自动重启中...')
                time.sleep(1)
                continue
            break
    except KeyboardInterrupt:
        exit()

if __name__=='__main__':
    run()
