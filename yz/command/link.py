from yz.command.Command import Manager
import os
import sys
import re
import json
from atexit import register as on_exit
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import dicts,trans_rep,set_rep,load_cq,rep_str
from tool.config import init_or_load_config, save_config

link_file = "links.json"

init = {
    "Command":{
        "links":{
            ".运行{A}":".py{A}",
            ".link[\\s]*\n{A}\nreply[\\s]*\n{B}": ".link\n{A}\nto\n.py back=0\ns = '{B}'\nMsg.send(s)",
            ".link[\\s]*\n{A}\nfunc[\\s]*\n{B}": ".link\n{A}\nto\n.py back=0\n{B}"
        }
    }
}

# 替换字符串的部分在rep_str

class link:
    level=4
    links = init_or_load_config(init)["Command"]["links"]
    link_pattern = re.compile("\s*\n([\S\s]+?)\nto[\s]*\n([\S\s]+)")
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)

        link_args={
            ' reload':link.reload_link,
            ' list':link.list_link,
            ' get ([0-9]+)':link.get_link,
            ' del ([0-9]+)':link.del_link
        }
        
        for k,v in link_args.items():
            cp = re.match(k,body)
            if cp:
                Msg.send(v(bot,cp))
                return
        
        try:
            body_match = link.link_pattern.match(load_cq(body).replace('\r\n','\n'))
            if body_match:
                link.add_link(body_match.group(1),body_match.group(2))
                Msg.send("设置成功")
                return
        except Exception as e:
            Msg.send(str(e))
            print(e)
            return
        Msg.send('格式: .link\\n...\\nto\\n...\n.link [reload | list | get <num> | del <num>]\n支持正则表达式，输入输出可填入替换符\n替换符:用大括号括起来的允许数字字母下划线作为命名的内容{\w+}，同时输入在输入输出时，将会匹配内容并替换，若命名开头为大写字母，则匹配包括空白符(包括换行符)在内的所有字符，否则仅匹配非空白符\ n输入输出可以包含同名替换符\n例子:\n.link\\n运行{A}\\nto\\n.py{A}')
    
    def add_link(rep,tar):
        link.links[rep]=tar
    
    def reload_link(bot,cp):
        link.links = init_or_load_config(init)["Command"]["links"]
        return f'重载了{len(link.links)}个link'

    @on_exit
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
    for k,v in reversed(link.links.items()):
        b = rep_str(k,v,s)        # 替换字符串的部分在rep_str
        if b:
            return b