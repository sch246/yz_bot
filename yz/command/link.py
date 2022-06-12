from functools import reduce
import os
import sys
import atexit
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import add_tab, find_all_CQ, load_CQ, make_CQ, re_mark, rep_str,cut_tab, tabbed
from tool.config import init_or_load_config, save_config
from tool.cmd import std_cmd
from tool.data import re_need_trans

link_file = "links.json"
#TODO 输入匹配柚子，输出匹配对方
#TODO 人能纠错对自己的称呼，这要求需要给每个人加上存储位置
#TODO 记忆力增强
#TODO 因果联系and(条件与层级) to or(并行与顺序执行)
    # 用link实现的话，，
init = {
    "Command":{
        "links":{
            ".运行{A}":".py{A}",
            ".ifcatch {a}":".py back=0\nbot.config.save_config({a},'Bot','ifcatch')\nbot.ifcatch={a}\nMsg.send('自动捕获设置为{a}')",
            ".dir( {A})?": ".py back=0\ns = os.listdir({A})\nMsg.send(s)",
            "柚子你?{A}": ".py back=0\nif 'group_id' in msg.keys() and 'card' in msg['sender'].keys():\n        s=msg['sender']['card']\nelse:\n    s=msg['sender']['nickname']\ns='你也{A}，'+s\nMsg.send(s)",
            ".savelog": ".py back=0\nbot.logger.save()\nMsg.send('保存完成')",
            ".savestorage": ".py back=0\nbot.storage.save_storage()\nMsg.send('保存完成')",
            "柚子$": ".py back=0\ns = '''我在'''\nMsg.send(s)",
            ".file read (?P<file>.*?)(?: (?P<from>\\-?[0-9]+) (?P<to>\\-?[0-9]+))?$": ".py back=0\ntry:\n    t=open({file}).read()\n    t='\\n'.join(t.splitlines()[{from}:{to}])\n    if isinstance(t,str):\n        if not t:\n            raise Exception('内容为空')\n        Msg.send('打开成功，内容如下:\\n'+t)\n    else:\n        raise Exception('不是字符串')\nexcept Exception as e:\n    Msg.send('打开失败，'+str(e))",
            ".file new (?P<File>.+)\n{Value}": ".py back=0\nif os.path.exists({File}):\n    Msg.send('文件已存在')\nelse:\n    open({File},'w',encoding='utf-8').write('''{Value}''')\n    Msg.send(f\"已创建文件'{ {File} }'\")",
            ".file write (?P<file>.*?)(?: (?P<from>\\-?[0-9]+) (?P<to>\\-?[0-9]+))?\r\n{A}": ".py back=0\ntry:\n    if not os.path.exists({file}):\n        raise Exception('文件不存在')\n    elif not os.path.isfile({file}):\n        raise Exception('路径存在，但不是文件')\n    t=open({file}).read()\n    sl=t.splitlines()\n    tarl='''{A}'''.splitlines()\n    p='\\n'.join(sl[{from}:{to}])\n    sl[{from}:{to}]=tarl\n    open({file},'w',encoding='utf-8').write('\\n'.join(sl))\n    Msg.send('写入成功')\nexcept Exception as e:\n    Msg.send('写入失败，\\n'+str(e))",
        }
    }
}

# 替换字符串的部分在rep_str

class link:  #TODO disable, off
    '''格式: .link (reload | save | list | get <num> | del <num>) | \\n<输入..>\\n(to | func | reply)\\n<输出..>
    用途: 建立输入映射，可以使输入A等价于输入B，但是注意，已存在的命令不会被link捕获
    例子\n================\n.link\n运行{A}\nto\n.py{A}\n================
    支持正则表达式，输入输出可填入替换符，输入输出前可以加4空格缩进
    替换符:用大括号括起来的允许数字字母下划线作为命名的内容{\w+}，同时输入在输入输出时，将会匹配内容并替换，若命名开头为大写字母，则匹配包括空白符(包括换行符)在内的所有字符，否则仅匹配非空白符\ n输入输出可以包含同名替换符
    替换符的原理是命名组，例如，在输入部分，(?P\<name>\S+)等价于第一个{name}，(?P\<A>[\S\s]+)等价于第一个{A}
    特殊替换(可用.link list查看): .link\\n<输入>\\n[reply | reply py | func]\\n<目标>
    分别为: 建立回复，建立py构成的字符串回复，建立简易的py调用'''
    docs={
    'run':__doc__,
    'err':'''格式: .link (reload | save | list | get <num> | del <num>) | \\n<输入..>\\n(to | func | reply)\\n<输出..>
    用途:注意，异常捕获状态下link的捕获不会被触发'''
    }
    level=4
    links = init_or_load_config(init)["Command"]["links"]
    
    @classmethod
    @std_cmd
    def run(cls):
        opr_cp = '(?P<opr>(?:to)|(?:func)|(?:reply))'
        return {
            ' reload':                                          cls.reload_link,
            ' save':                                            cls.save_link,
            ' list':                                            cls.list_link,
            ' get (-?[0-9]+)':                                  cls.get_link,
            ' del (-?[0-9]+)':                                  cls.del_link,
            f'\s*\r\n([\S\s]+?)\r\n{opr_cp}[\s]*\r\n([\S\s]+)': cls.add_link,
        }
        
    def err(bot, body: str, msg: dict):
        return link.run(bot, body, msg)
    
    def add_link(bot,match):
        k = match.group(1).rstrip()
        v = match.group(3)
        if tabbed(k): k=cut_tab(k)
        if tabbed(v): v=cut_tab(v)
        # 如果key有CQ码，为正则表达式进行转码
        CQ_list = find_all_CQ(k)
        if CQ_list:
            for CQ in CQ_list:
                tar = reduce(lambda x, y: x.replace(y,'\\'+y),re_need_trans,CQ)
                k = k.replace(CQ,tar)
        if k and isinstance(v,str):
            opr = match.group('opr').strip()
            if link.add(link.links, k, v, opr):
                return '设置成功'
            else:
                return f'设置失败，错误的中间词"{repr(opr)}"'
        return f'设置失败\ncp={match}\ngroup1={match.group(1)}\ngroup2={match.group(2)}'
    
    def add(d, k, v, opr):
        dic={
            'to':f'{v}',
            'func':f'.py back=0\n{v}',
            'reply':f".py back=0\ns = '''{v}'''\nMsg.send(s)"
        }
        if opr in dic.keys():
            d[k]=dic[opr]
            return True
        else:
            return False
    
    def reload_link(bot,match):
        link.links = init_or_load_config(init)["Command"]["links"]
        return f'重载了{len(link.links)}个link'


    def save_link(bot,match):
        link._save_link()
        return f'保存了{len(link.links)}个link'

    def _save_link():
        save_config(link.links,'Command','links')
        
        
    def list_link(bot,match):
        link_str_dic = link.links
        reply = ''
        index = 0
        for k,v in link_str_dic.items():
            reply += prn_key(index,k,v)
            index += 1
        return reply
        
    def get_link(bot,match):
        index = int(match.group(1)) % len(link.links)
        dic = link.links
        k = list(dic.keys())[index]
        v = dic[k]
        return prn(index,k,v)
        
    def del_link(bot,match):
        index = int(match.group(1)) % len(link.links)
        dic = link.links
        k = list(dic.keys())[index]
        v = dic[k]
        del link.links[k]
        return '已删除:\n'+prn(index,k,v)

def prn_key(index, k,v):
    # k = k.replace('\\n','\n').replace('\\r','\r')
    return f'\n█ {index}\n{k}'
def prn(index, k,v):
    # k = k.replace('\\n','\n').replace('\\r','\r')
    return f'\n█ {index}\n{add_tab(k)}\nto\n{add_tab(v)}\n'


def match(bot, event):
    Msg=bot.api.Create_Msg(bot,**event)
    s = event['raw_message']
    print('待匹配:',repr(s))
    for k,v in reversed(list(link.links.items())):
        if k:
            b = rep_str(k,v,s)        # 替换字符串的部分在rep_str
            if b: # 匹配成功
                if bot.state[0]=='err':
                    Msg.send('当前处于异常捕获状态，link匹配已关闭\n.help以查看帮助')
                    return False
                else:
                    return b           # 警告: 可能出现循环bug
def CQ2re(CQ:str):
    '''将单个CQ码转化为能捕获它的正则表达式'''
    if CQ.startswith('[CQ:image'):
        dCQ = load_CQ(CQ)
        dCQ['data'] = {'file':dCQ['data']['file']}
        CQ = make_CQ(dCQ)
    return re_mark(CQ)


# 猜猜为什么不用装饰器？因为python3.7.9使用装饰器就不能正常使用atexit._run_exitfuncs()了
atexit.register(link._save_link)