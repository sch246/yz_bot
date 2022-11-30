'''每次启动时在_code/bot/cmds/py.py加载'''

if (lambda:False)():
    # 纯粹是为了不要编辑器显示警告，运行是不可能的
    from _code.bot.cmds.py import *

# 用于.link set2的捕获类型设置，举例: {a:Int}
Int = r'(?:0|-?[1-9]\d*)'
Name = r'\w+'
Param = r'\S+'
All = r'[\S\s]+'
CQ = r'\[CQ:[^,\]]+(?:,[^,=]+=[^,\]]+)*\]'


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
mc_worldname = 'world'
mc_packformat = 6

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
    _msg = msg
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
            sendmsg(str_tool.remove_emptyline(log),**_msg)
        if rcon or 'RCON running' in log:
            rcon = True
            if connect_mc():
                return '启动完毕'
        time.sleep(3)
        t += 3
    sendmsg('超时',**_msg)

def stop_mc(close_screen=False,timeout=30):
    _msg = msg
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
        if log:
            sendmsg(str_tool.remove_emptyline(log),**_msg)
        if 'All dimensions are saved' in log:
            sendmsg('All dimensions are saved',**_msg)
            break
        time.sleep(3)
        t += 3
    if close_screen:
        return screen.stop(mc_screen)
    return 'MC已关闭'

def mc_restart():
    if stop_mc() and start_mc():
        return '重启完毕'
    return '失败'


class Pack:
    '''mc datapck'''
    packdir = os.path.join(mc_path, mc_worldname, 'datapacks')
    def __init__(self, name, description='a datapack') -> None:
        self.name = name
        self.path = os.path.join(Pack.packdir, name)
        if not os.path.isdir(self.path):
            os.mkdir(self.path)
            file.json_write(os.path.join(self.path,'pack.mcmeta'),
                {'pack_format':mc_packformat,'description':description})
    @staticmethod
    def _get_end(type):
        if type=='functions':
            return 'mcfunction'
        else:
            return 'json'
    def _getpath(self, type:str, path:str):
        lst = path.split(':')
        c = len(lst)
        if c==1:
            namespace = 'minecraft'
            path = path
        elif c==2:
            namespace, path = lst
        else:
            raise ValueError('冒号太多')
        if namespace=='':
            namespace = 'minecraft'
        if path=='':
            raise ValueError('没有名字')
        return os.path.join(self.path, 'data', namespace, type, path+'.'+self._get_end(type))
    def func_set(self, name, value):
        path = self._getpath('functions', name)
        os.makedirs(os.path.split(path)[0],exist_ok=True)
        file.write(path,value)
    def func_get(self, name):
        return file.read(self._getpath('functions', name))
    def func_del(self, name):
        os.remove(self._getpath('functions', name))
    def tag_func_add(self, func:str, tag:str):
        tagpath = self._getpath('tags/functions', tag)
        if not os.path.exists(tagpath):
            J = {'replace':False,'values':[]}
            file.json_write(tagpath,J)
        else:
            J = file.json_read(tagpath)
        if func not in J['values']:
            J['values'].append(func)
            file.json_write(tagpath,J)
    def tag_func_del(self,func:str,tag:str):
        tagpath = self._getpath('tags/functions', tag)
        if not os.path.exists(tagpath):
            return
        J = file.json_read(tagpath)
        if func in J['values']:
            J['values'].remove(func)
        if not J['values']:
            os.remove(tagpath)
        else:
            file.json_write(tagpath,J)

    def _exec(self, hold):
        head, lines = hold
        if head:
            oper, name, *_ = head.split(' ')
            if oper=='#set':
                self.func_set(name, '\n'.join(lines))
            elif oper=='#del':
                self.func_del(name)
            elif oper=='#tagadd':
                for func in lines:
                    self.tag_func_add(func, name)
            elif oper=='#tagdel':
                for func in lines:
                    self.tag_func_del(func, name)

    def read(self, text:str):
        lines = text.splitlines()
        hold = ['',[]]
        for line in lines:
            if not re.match(r'#(set|del|tagadd|tagdel) \S',line):
                if hold[0]:
                    hold[1].append(line.strip())
            else:
                self._exec(hold)
                hold[0] = line
                hold[1] = []
        self._exec(hold)
