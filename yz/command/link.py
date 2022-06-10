from yz.command.Command import Manager
import os
import sys
import re
import json
import atexit
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import dicts,trans_rep,set_rep,load_cq,rep_str,cut_tab
from tool.config import init_or_load_config, save_config

link_file = "links.json"

init = {
    "Command":{
        "links":{
            ".运行{A}":".py{A}",
            ".link[\\s]*\n{A}\nreply[\\s]*\n{B}": ".link\n{A}\nto\n.py back=0\ns = '{B}'\nMsg.send(s)",
            ".link[\\s]*\n{A}\nreply py[\\s]*\n{B}": ".link\n{A}\nto\n.py back=0\ns = {B}\nMsg.send(s)",
            ".link[\\s]*\n{A}\nfunc[\\s]*\n{B}": ".link\n{A}\nto\n.py back=0\n{B}"
        }
    }
}

# 替换字符串的部分在rep_str

class link:
    '''格式: .link\\n<输入>\\nto\\n<目标>
    .link [reload | list | get <num> | del <num>]
    用途: 建立输入映射，可以使输入A等价于输入B
    例子: .link\\n运行{A}\\nto\\n.py{A}
    支持正则表达式，输入输出可填入替换符
    替换符:用大括号括起来的允许数字字母下划线作为命名的内容{\w+}，同时输入在输入输出时，将会匹配内容并替换，若命名开头为大写字母，则匹配包括空白符(包括换行符)在内的所有字符，否则仅匹配非空白符\ n输入输出可以包含同名替换符
    替换符的原理是命名组，例如，在输入部分，(?P\<name>\S+)等价于第一个{name}，(?P\<A>[\S\s]+)等价于第一个{A}
    特殊替换(可用.link list查看): .link\\n<输入>\\n[reply | reply py | func]\\n<目标>
    分别为: 建立回复，建立py构成的字符串回复，建立简易的py调用'''
    level=4
    links = init_or_load_config(init)["Command"]["links"]
    link_pattern = re.compile("\s*\n([\S\s]+?)\nto[\s]*\n([\S\s]+)")
    link_pattern2 = re.compile("\s*\n(((?:\s*\n)|(?:    .*\n))+)to[\s]*\n(((?:\s*\n)|(?:    .*\n))+)")
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)
        body = load_cq(body).replace('\r\n','\n')

        link_args={
            ' reload':link.reload_link,
            ' list':link.list_link,
            ' get ([0-9]+)':link.get_link,
            ' del ([0-9]+)':link.del_link,
            '\s*((?:(?:\n\s*)|(?:\n    .*))+)\nto[\s]*((?:(?:\n\s*)|(?:\n    .*))+)':link.tab_add_link,
            '\s*\n([\S\s]+?)\nto[\s]*\n([\S\s]+)':link.add_link,
        }
        
        for k,v in link_args.items():
            cp = re.match(k,body)
            if cp:
                Msg.send(v(bot,cp))
                return
            
        Msg.send(link.__doc__)
        
    def tab_add_link(bot,cp):
        link.links[cut_tab(cp.group(1))]=cut_tab(cp.group(2))
        return '设置成功'
    
    def add_link(bot,cp):
        link.links[cp.group(1)]=cp.group(2)
        return '设置成功'
    
    def reload_link(bot,cp):
        link.links = init_or_load_config(init)["Command"]["links"]
        return f'重载了{len(link.links)}个link'


    def save_link():
        save_config(link.links,'Command','links')
        
        
    def list_link(bot,cp):
        link_str_dic = link.links
        reply = ''
        index = 0
        for k,v in link_str_dic.items():
            reply += prn(index,k,v)
            index += 1
        return reply
        
    def get_link(bot,cp):
        index = int(cp.group(1))
        dic = link.links
        k = list(dic.keys())[index]
        v = dic[k]
        return prn(index,k,v)
        
    def del_link(bot,cp):
        index = int(cp.group(1))
        dic = link.links
        k = list(dic.keys())[index]
        v = dic[k]
        del link.links[k]
        return '已删除:\n'+prn(index,k,v)

def prn(index, k,v):
    return f'\n█[{index}]\n{k}\n--->>\n{v}\n'


def match(s):
    for k,v in reversed(list(link.links.items())):
        if k:
            b = rep_str(k,v,s)        # 替换字符串的部分在rep_str
            if b:
                return b


atexit.register(link.save_link)