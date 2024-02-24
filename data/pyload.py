if (lambda:False)():
    # 纯粹是为了不要编辑器显示警告，运行是不可能的
    from _code.bot.cmds.py import *

Int = r'(?:0|-?[1-9]\d*)'
Name = r'\w+'
Param = r'\S+'
All = r'[\S\s]+'
CQ = r'\[CQ:[^,\]]+(?:,[^,=]+=[^,\]]+)*\]'

def rd(r,d):
    '''掷骰子'''
    return sum(random.randint(1, d) for _ in range(r))

###
早 = ['早啊，$name','早上好!','你也早！','早']
###
你也好=['你也好！','w','你好呀，$name']
###
Signs='？！~:;!\?\.'
###

rcon_address = '0.0.0.0'
rcon_port = 25575
rcon_password = '12345ssdlh'

localmc= mc.mc(rcon_address, rcon_port, rcon_password)
mc_screen = 'mc'
mc_path = '/opt/mc1192'
mc_startbash = './run.sh'
mc_worldname = 'world2'
mc_packformat = 6

def connect_mc():
    try:
        return localmc.connect()
    except:
        return False

def checkmc():
    ret = 'rcon未连接'
    try:
        ret = localmc.send('test')
    except:
        pass
    if ret!='rcon未连接': # 如果一切正常
        return 2
    elif connect_mc(): # 如果MC是开着的
        return 1
    else:
        os.system(f'screen -ls|grep .{mc_screen} > data/tmp.txt')
        s = file.read('data/tmp.txt')
        if s:
            return 0 # 有screen
        else:
            return -1 # screen 未开启

def start_mc(timeout=120):
    sendmsg('开始启动服务器')
    t = 0
    i = checkmc()
    if i>0:
        return 'MC已连接'
    rcon = False
    if i == -1:
        screen.start(mc_screen)
        i = 0
    screen.pop(mc_screen)
    screen.send(mc_screen,f'cd {mc_path} && {mc_startbash}')
    while t<timeout:
        log = screen.pop(mc_screen)
        if log:
            sendmsg(log)
        if rcon or 'RCON running' in log:
            rcon = True
            if connect_mc():
                return '启动完毕'
        time.sleep(3)
        t += 3
    sendmsg('超时')

def stop_mc(close_screen=False,timeout=30):
    sendmsg('开始关闭服务器')
    i = checkmc()
    if i==-1:
        return 'screen和MC已是关闭状态'
    if i==0:
        if close_screen:
            return screen.stop(mc_screen)
        return 'MC已是关闭状态'
    localmc.send('stop')
    localmc.close()
    t = 0
    while t<timeout:
        log = screen.pop(mc_screen)
        if log:
            sendmsg(log)
        if 'All dimensions are saved' in log:
            sendmsg('All dimensions are saved')
            break
        time.sleep(3)
        t += 3
    if close_screen:
        return screen.stop(mc_screen)
    return 'MC已关闭'

def restart_mc():
    if stop_mc() and start_mc():
        return '重启完毕'
    return '失败'

import inspect
###

def nim(code:str):
    file.write('data/tmp.nim',code)
    return os.popen('nim compile --verbosity:0 --hints:off --run "data/tmp.nim"').read()

###



###
connect_mc()
###
def ls(obj):
    return '\n'.join(sorted(list(map(str,obj))))
###
def at2qq(at):
    return cq.load(at)['data']['qq']
###
import math
def sun_theta(t):
    day = time.gmtime(t).tm_yday
    year = time.gmtime(t).tm_year
    N0 = 79.6764+0.2422*(year-1985)-int((year-1985)/4)
    return 2*math.pi*(day-N0)/365.2422

def sun_delta(t):
    θ = sun_theta(t)
    return 0.0028-1.9857*math.sin(θ)+9.9059*math.sin(2*θ)-7.0924*math.cos(θ)-0.6882*math.cos(2*θ)
###
def 真太阳时(t, 经度):
    delta = sun_delta(t)*60
    d2 = 经度*4*60
    return time.gmtime(t+delta+d2)
###

def my_product(*seqs):

    seq , *seqs = seqs

    if not seqs:

        for item in seq:

            yield (item,)

        return

    for item in seq:

        for others in my_product(*seqs):

            yield (item,) + others

###