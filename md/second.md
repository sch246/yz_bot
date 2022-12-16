# 柚子bot

bot，第一次重构，由于古镇天的建议

虽然我又想重构了，不过鉴于QQ的历史记录功能太差了所以还是加紧让它能用吧，不用来干啥主要是用来记录聊天

这个bot没有异步，性能不高，模块耦合较重，不过这些都不是我想重构的原因，关键是我想到了更好的bot编程方法，这个就是题外话了

这个bot还是实现了一些令人激动的功能的

- bot的github地址: https://github.com/sch246/yz_bot

## .py命令

我想尽量减少进入后台操作bot的次数，很多操作可以通过和bot发信息完成嘛

而且自编程也是令人激动的，于是就有了.py命令

最初的.py只是简单地在循环里面检测.py开头然后exec的

不过现在放进Command模块下了

以下是目前.py的帮助信息

> 格式: .py < code>
>
> 用途: 执行py代码
>
> 给out赋值以决定将返回的结果(默认None)，给back赋值以决定是否返回结果(默认True)
>
> 让bot发送和接收消息可以用Msg.send(< msg>)和Msg.recv(< msg>)，具体可以使用.py out=Msg.__doc__来查看
>
> msg是当前被执行消息的msg字典
>
> 可以使用locals()或globals()来查看局部或全局变量

### msg_locals

Storage模块下的用于存储.py的运行环境的字典

在bot关闭时会将其作为文件自动保存，bot启动时也会从文件加载它

也就是说其中的变量是可以一直存在下去的，除非关了bot再删文件

它存储的位置是./storage/msg_locals.pkl文件，并没有每个变量一个文件存储

它是序列化的非文本文件，因此无法从后台直接编辑，但是可以通过和bot发送信息使用.py命令以查看和修改它

保存时会跳过不可保存的文件并打印在后台

### Msg

由api模块的Create_Msg创建

接收bot和msg参数，所以直接使用Msg.send和Msg.recv可以将消息发到准确的地方

## .reboot命令

用于重启bot，这可以用于无脑加载全部的修改，因为实际上重启很快所以没什么感觉##，我日常改一点东西就频繁使用##

这里一个花了点功夫的地方就是重启完成后bot会回复一句重启完成

原理也很简单，既然中间自己关闭了，那么就通过文件来传递就好了

为此添加了initfunc功能

### initfunc

是Storage模块内的另一个自动保存自动加载的字典，并且会在bot开始接受信息前依次运行其中的函数，不过不同的是，自动加载的initfunc并不会自动保存

这意味着initfunc内的内容一般是一次性的，除非在这个func内再把自己添加进initfunc列表

需要转移的信息只有msg参数和将发送的文本，bot参数是不能转移的，因为里面的ws对象会变

所以让函数接受bot作为参数就行

所以reboot命令长这样

```python title="reboot.py"
import sys
import os
import atexit

class reboot:
    '''格式: .reboot
    用途: 重启bot'''
    level=4
    def run(bot, body: str, msg: dict):
        print('重启中')
        bot.api.Create_Msg(bot,**msg).send('重启中')
        def reboot_hello(bot):
            bot.api.Create_Msg(bot,**msg).send('重启完成')
        bot.storage.add_initfunc('reboot_hello',reboot_hello)
        atexit._run_exitfuncs()
        python = sys.executable
        os.execl(python, python, *sys.argv)
```

## .link命令

这也是一个非常令人激动的命令，与.py配合可以发挥出非常大的效果

它的用途是截取输入并转化成新的输入，并且支持正则表达式和`替换符`

因为想不到什么名字所以就用`替换符`来指代了

以下是.link的帮助信息

> 格式: .link\\n...\\nto\\n...
>
> .link [reload | list | get < num> | del < num>]
>
> 用途: 建立输入映射，可以使输入A等价于输入B
>
> 例子: .link\\n运行{A}\\nto\\n.py{A}
>
> 支持正则表达式，输入输出可填入替换符
>
> 替换符:用大括号括起来的允许数字字母下划线作为命名的内容{\w+}，同时输入在输入输出时，将会匹配内容并替换，若命名开头为大写字母，则匹配包括空白符(包括换行符)在内的所有字符，否则仅匹配非空白符\ n输入输出可以包含同名替换符
>
> 替换符的原理是命名组，例如，在输入部分，(?P< name>\S+)等价于第一个{name}，(?P< A>[\S\s]+)等价于第一个{A}

### rep_str

link命令的基础在于替换符

要实现这个我第一时间想到了re的命名组，于是就做了）

以下是实现替换的函数

```python:v-pre
def trans_rep(src_rep:str):
    src_ = re.compile('{(\w+?)}')
    keys = set()
    def f(match:re.Match):
        key = match.group(1)
        if key in keys:
            rtn = f'(?P={key})'
        else:
            if key[0].isupper():
                rtn= f'(?P<{key}>[\S\s]+)'
            else:
                rtn= f'(?P<{key}>\S+)'
        keys.add(key)
        return rtn
    # 得检测重复的group并替换成引用
    return src_.sub(f,src_rep)

def rep_str(rep:str, tar:str, src:str):
    re_rep = re.compile(trans_rep(rep))
    match = re_rep.match(src)
    if not match:
        return False
    else:
        for key, value in match.groupdict().items():
            tar = tar.replace(f'{{{key}}}',value)
        return tar

def set_rep(rep:str, tar:str):
    return lambda src:rep_str(rep,tar,src)
```

```python:v-pre
>>> f = set_rep('{a}{a}{name}','{name}:不要{a}{name}!')        
>>> f('打打柚子') 
'柚子:不要打柚子!'
```

由于原理是命名组，因此使用(?P< name>xx)使用正则表达式来捕获也是可行的

### 命令创建器

使用link甚至可以创造出新的命令

link检测不同部分的正则表达式是

```
\s*\n([\S\s]+?)\nto[\s]*\n([\S\s]+)
```

所以它会捕获最近的一个`\nto\n`

稍加修改可以让被捕获的命令允许含有to，不过那是后话了

以下为建立问答命令的命令

```text:v-pre
.link
.link[\s]*
{A}
reply[\s]*
{B}
to
.link
{A}
to
.py back=0
s = {B}
Msg.send(s)
```

接着只需

```text:v-pre
.link
柚子{a}
reply
'你也{a}，'+msg['sender']['nickname']
```

于是之后群里有人说`柚子早安`时bot就会回复`你也早安，{名字}`了

::: warning 注意

py命令按照常理不是谁都能用的，所以之后可能需要对link进行更改

至于现在，还没设置权限系统，随便啦

:::

