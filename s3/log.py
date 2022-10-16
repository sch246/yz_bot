'''log模块，不同于聊天记录模块，重心在于debug'''
# print('load log!')
import logging
import os.path
import time
import sys

in_debug = 'debug' in sys.argv[1:]

level = logging.DEBUG if in_debug else logging.INFO

fmt = '[%(levelname)s]%(message)s' if in_debug else '[%(asctime)s][%(levelname)s]%(message)s'
logging.basicConfig(level=level, format=fmt)
logger = logging.getLogger("柚子")
log_path = 'logs'
log_file = os.path.join(log_path, f'{time.strftime(r"%Y-%m-%d")}.log')
if not os.path.isdir(log_path):
    os.mkdir(log_path)
formatter = logging.Formatter(fmt)

handler = logging.FileHandler(log_file, encoding='utf8')
handler.setFormatter(formatter)
logger.addHandler(handler)


def wrap(f):
    def _(title, msg, *args, **kargs):
        pre = f'[{title}]' if title else ''
        return f(f'{pre} {msg}', *args, **kargs)
    return _


debug = wrap(logger.debug)          # 调试过程中使用DEBUG等级，如算法中每个循环的中间状态
info = wrap(logger.info)            # 处理请求或者状态变化等日常事务
warn = wrap(logger.warn)            # 发生很重要的事件，但是并不是错误时，如用户登录密码错误
error = wrap(logger.error)          # 发生错误时，如IO操作失败或者连接问题
critical = wrap(logger.critical)    # 特别糟糕的事情，如内存耗尽、磁盘空间为空，一般很少使用
fatal = wrap(logger.fatal)          # 致命错误
