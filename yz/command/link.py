from functools import reduce
import os
import sys
import atexit
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import add_tab, find_all_CQ, insert_str, load_CQ, make_CQ, re_mark, rep_str,cut_tab, tabbed, re_CQdatas, _re_CQ
from tool.config import init_or_load_config, save_config
from tool.cmd import std_cmd
from tool.data import re_need_trans

link_file = "links.json"
#TODO 小豆想bot能更好地打招呼
    # 输入匹配柚子，输出匹配对方, 运行函数生成
    #TODO 人能纠错对自己的称呼
        # 这要求需要给每个人加上存储位置
        # 记忆力增强
#TODO 雨弓有时会禁言bot
    # 设置link disable和off，对特定群临时关闭的功能
#TODO 调试需求
    # try命令，debug用，返回语句匹配的是哪一句，以及link调用路径
#TODO 每条命令都匹配一个池的分类是不科学的，而且目前还是列表中只运行一个的状态(设想中应该是所有的都尝试执行)，还是单线程
    #  link数据包
    # 因果联系(and to or)(条件与层级 to 并行与顺序执行)
#TODO link reply分多句匹配，以及多句间隔的命令"匹配开始""匹配结束"
init = {
    "Command":{
        "links":{
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
            f'\s*\r?\n([\S\s]+?)\r?\n{opr_cp}[\s]*\r?\n([\S\s]+)': cls.add_link,
        }
        
    def err(bot, body: str, msg: dict):
        return link.run(bot, body, msg)
    
    def add_link(bot,match):
        k = match.group(1).rstrip()
        v = match.group(3)
        if tabbed(k): k=cut_tab(k)
        if tabbed(v): v=cut_tab(v)
        
        # 如果key有CQ码，为正则表达式进行转码
        k = _re_CQ.sub(lambda match:CQ2re(match.group()),k)
                
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
            'func':f'.py\n{v}\nNone',
            'reply':f".py\ns = '''{v}'''\ns"
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
        reCQ = re_mark(make_CQ(dCQ))
        return insert_str(reCQ,re_CQdatas,-2)
    else:
        return re_mark(CQ)

def re2CQ(reCQ):
    '''把CQ2re函数的结果转化回去\n注意: 不保证等价'''
    


# 猜猜为什么不用装饰器？因为python3.7.9使用装饰器就不能正常使用atexit._run_exitfuncs()了
atexit.register(link._save_link)