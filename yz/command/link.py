import os
import sys
import atexit
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import add_tab, rep_str,cut_tab
from tool.config import init_or_load_config, save_config
from tool.cmd import std_cmd

link_file = "links.json"

init = {
    "Command":{
        "links":{
            ".运行{A}":".py{A}",
            ".ifcatch {a}":".py back=0\nbot.config.save_config({a},'Bot','ifcatch')\nbot.ifcatch={a}\nMsg.send('自动捕获设置为{a}')",
            ".dir( {A})?": ".py back=0\ns = os.listdir({A})\nMsg.send(s)",
            "柚子你?{A}": ".py back=0\nif 'group_id' in msg.keys() and 'card' in msg['sender'].keys():\n        s=msg['sender']['card']\nelse:\n    s=msg['sender']['nickname']\ns='你也{A}，'+s\nMsg.send(s)",
            ".savelog": ".py back=0\nbot.logger.save()\nMsg.send('保存完成')",
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
        tab_cp = '((?:(?:\n\s*)|(?:\n    .*))+)'
        opr_cp = '(?P<opr>(?:to)|(?:func)|(?:reply))'
        return {
            ' reload':                                      cls.reload_link,
            ' save':                                        cls.save_link,
            ' list':                                        cls.list_link,
            ' get (-?[0-9]+)':                              cls.get_link,
            ' del (-?[0-9]+)':                              cls.del_link,
            f'\s*{tab_cp}\n{opr_cp}[\s]*{tab_cp}':          cls.tab_add_link,
            f'\s*\n([\S\s]+?)\n{opr_cp}[\s]*\n([\S\s]+)':   cls.add_link,
        }
        
    def err(bot, body: str, msg: dict):
        return link.run(bot, body, msg)
        
    def tab_add_link(bot,match):
        k = cut_tab(match.group(1))
        v = cut_tab(match.group(3))
        if k and isinstance(v,str):
            opr = match.group('opr').strip()
            if link.add(link.links, k, v, opr):
                return '设置成功'
            else:
                return f'设置失败，错误的中间词"{repr(opr)}"'
        return f'设置失败\ncp={match}\ngroup1={match.group(1)}\ngroup2={match.group(2)}\nk={k}\nv={v}'
    
    def add_link(bot,match):
        k=match.group(1)
        v = match.group(3)
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
            reply += prn(index,k,v)
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

def prn(index, k,v):
    return f'\n█ {index}\n{add_tab(k)}\nto\n{add_tab(v)}\n'


def match(bot, event):
    Msg=bot.api.Create_Msg(bot,**event)
    s = event['raw_message']
    for k,v in reversed(list(link.links.items())):
        if k:
            b = rep_str(k,v,s)        # 替换字符串的部分在rep_str
            if b: # 匹配成功
                if bot.state[0]=='err':
                    Msg.send('当前处于异常捕获状态，link匹配已关闭\n.help以查看帮助')
                    return False
                else:
                    return b           # 警告: 可能出现循环bug


# 猜猜为什么不用装饰器？因为python3.7.9使用装饰器就不能正常使用atexit._run_exitfuncs()了
atexit.register(link._save_link)