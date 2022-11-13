if (lambda:False)():
    # 纯粹是为了不要编辑器显示警告，运行是不可能的
    from _code.bot.cmds.py import *

# 用于.link set2的捕获类型设置，举例: {a:Int}
Int = r'(?:0|-?[1-9]\d*)'
Name = r'\w+'
Param = r'\S+'
All = r'[\S\s]+'



def rd(r,d):
    '''掷骰子'''
    return sum(random.randint(1, d) for _ in range(r))

# mc启动函数，配置好后只需要无脑 start_mc() 和 stop_mc() 就行了

# rcon连接设置
rcon_address = '0.0.0.0'
rcon_port = 25575
rcon_password = '123456'

mc_screen = 'mc'            # 想要mc在名叫什么的screen里运行
mc_path = '/opt/mc1192'     # mc的路径
mc_startbash = './run.sh'   # 相对于mc的路径，它的启动脚本在哪里

def connect_mc():
    try:
        return mc.connect(rcon_address, rcon_port, rcon_password)
    except:
        return False

def checkmc():
    ret = 'rcon未连接'
    try:
        ret = mc.send('test')
    except:
        pass
    if ret!='rcon未连接': # 如果一切正常
        return 2
    elif connect_mc(): # 如果MC是开着的
        return 1
    else:
        if os.popen(f'screen -ls|grep .{mc_screen}').read():
            return 0 # 有screen
        else:
            return -1 # screen 未开启

def start_mc(timeout=120):
    t = 0
    i = checkmc()
    if i>0:
        return 'MC已连接'
    rcon = False
    if i == -1:
        screen.start(mc_screen)
        i = 0
    screen.pop_log(mc_screen)
    screen.send(mc_screen,f'cd {mc_path} && {mc_startbash}')
    while t<timeout:
        log = screen.pop_log(mc_screen)
        if log:
            sendmsg(str_tool.remove_emptyline(log))
        if rcon or 'RCON running' in log:
            rcon = True
            if connect_mc():
                return '启动完毕'
        time.sleep(1)
        t += 1
    sendmsg('超时')

def stop_mc(close_screen=False,timeout=30):
    i = checkmc()
    if i==-1:
        return 'screen和MC已是关闭状态'
    if i==0:
        if close_screen:
            return screen.stop(mc_screen)
        return 'MC已是关闭状态'
    mc.send('stop')
    mc.close()
    t = 0
    while t<timeout:
        log = screen.pop_log(mc_screen)
        if 'All dimensions are saved' in log:
            sendmsg('All dimensions are saved')
            break
        time.sleep(1)
        t += 1
    if close_screen:
        return screen.stop(mc_screen)
    return 'MC已关闭'

def mc_restart():
    if stop_mc() and start_mc():
        return '重启完毕'
    return '失败'


