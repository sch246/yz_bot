# [柚子bot](https://sch246.com/5-yz_bot.html)


- [github项目地址](https://github.com/sch246/yz_bot)

第三次重构，经历了从 ws 通信改成 http 通信又改成 ws 通信最后又改回 http 通信的过程

关于安装过程就省略了，在 github 的 README 有写，这里谈怎么使用

## 启动

无论群聊或私聊，管理员对 bot 发送`.reboot`和`.shutdown`即可重启和关闭 bot

> 重启可以快速应用新的源代码，对 bot 调试非常方便

进入 bot 的目录并且使用 python3.10 以上运行 `run.py`可以启动 bot，可以加上`-h`参数查看可以加哪些参数

```
接受的参数:
    -h              显示本提示
    -a              当出现异常时自动重启
    --auto_reboot   同上
    -l              仅记录log
    --log_only      同上
    -q 5700         发送端口, 对应 go-cqhttp 的 HTTP 监听地址
    -p 5701         监听端口, 对应 go-cqhttp 的 HTTP POST 地址
    debug           debug模式(并没有什么区别)
```

> 也可以使用`_code/main.py`启动，不过那样将失去重启功能

bot 使用 http 通信，连接 go-cqhttp 的地址需要在 `_code/bot/connect_with_http.py`设置
##我从来没有想过 bot 需要开多个号的状况！也没有想过可能有多个bot在同个群的状况！(叉腰)##

## 文件结构

使用 柚子bot 会非常容易涉及到文件上的操作

不用源代码运行的 柚子bot 是没有灵魂的！

文件目录大概是这个结构

```
> _code         # bot自身的源代码，可编辑
> chatlog       # bot保存的聊天记录，可查看
> data          # bot存储的自身和用户的数据，可编辑
config.json     # bot的基础设定
run.py          # 启动bot的py脚本
```

`config.json`文件存储的是管理员(op, operator)和 bot 自身的昵称(nickname)，都是列表

data下的文件结构如下，除了`pyload.py`之外，删掉不影响启动

```
v data
    > screens           # 打开的screen会保存log到这里
    v storage           # s3.storage 模块管理的区域
        > groups        # 群的存储
        > users         # 玩家的个人存储
        cave.json       # 回声洞的数据保存到这里
        links.json      # .link 命令管理的links

    cache_msgs          # bot.cache 的消息存储，每个聊天范围只存储最近的256条
    pyload.py           # .py 命令初始化时会运行的文件，里面也有一部分设置
    (reboot_greet.py)   # .reboot 命令运行时的临时存储，可能不存在
    (shutdown_greet.py) # .shutdown命令运行时的临时存储，可能不存在
```

然后是`_code`部分，这里是 bot 的源代码，但是也有用于编辑的部分

一般来讲需要添加功能都可以通过`.py`和`.link`来实现，，不过通过直接添加命令有时候可能更直观一些

```
v _code
    v bot               # bot相关的模块
        > cmds          # 定义bot的命令，以.开头，优先级比.link创建的高
        ...
    > s3                # 独立于bot也能有用的模块
    main.py             # bot真正的运行脚本，控制bot核心运行
```
### [添加命令](/md/use/cmd.md)

## 基本命令

### [.help](/_code/bot/cmds/help.py)

可以用来`.help`查看所有命令及其简略说明

或者`.help <命令名>`来查看命令的详细解释

### [.test](/_code/bot/cmds/test.py)

基础命令，可以用来测试 bot 还在不在

### [.echo](/_code/bot/cmds/echo.py)

基础命令，用于复读用户发送的消息

### [.reboot](/_code/bot/cmds/reboot.py)(需要权限)

用于远程重启 bot，重启后会应用对源代码的更改

bot 在运行期间不会读取源代码，也就是甚至可以在运行期间把`_code`文件夹删掉

但是如果重启时没有找到`_code`文件夹，或者更改后的源代码有错误，bot 会重启失败(废话)

重启后会发出问好

> 在 bot 运行时运行 python 代码`exit(233)`也可以达成重启的效果，只是会没有问好
>
> 用`.py`命令做不到上述效果，因为`.py`是多线程，`exit`只会关掉自己的线程

### [.shutdown](/_code/bot/cmds/shutdown.py)(需要权限)

同上，bot会在下次启动时发出问好

### [.op](/_code/bot/cmds/op.py)(需要权限)

```
.op [del] (<qq号:int> | <at某人:cq[at]>)+
```

中间可换行

用于便捷管理权限的命令，同 mc 的`/op`,管理员可以赋予和解除管理员，同时具有所有命令(包括高度危险的命令)的使用权限，因此赋予需谨慎

`.op`命令不能解除 master 的权限，但是防君子不防小人

### [.file](/_code/bot/cmds/file.py)(需要权限)

用于操作文件的命令，可以被`.py`命令取代，不过还是姑且留着

注意，以下不是 bnf 语法，`||`表示分条消息

```
.file
 : read <文件路径> [<起始行> <结束行>]
 | write <文件路径> [<起始行> <结束行>]\n<内容>
 | get <文件路径>
 | set <文件路径> || <文件/图片>
<文件> || .file to <文件路径>
```

read 用于读取文本文件，可以指定行号范围，并且它会显示行号

write 可以写回去，写的时候如果附带了行号会自动去掉

> read 可以读取文件夹，将会列出文件夹内的内容

get 和 send 和 to 用来发送和接收文件，不过似乎不怎么能用


## 核心命令

### [.py](/_code/bot/cmds/py.py)(需要权限)

```
.py <内容>
```

在多线程运行 python 代码，可以调用 bot 的几乎任何代码，发送和接收消息，管理数据

可以直接调用这些文件内定义的东西: [py.py](/_code/bot/cmds/py.py) [main.py](/_code/main.py) [funcs.py](/_code/funcs.py) [pyload.py](/data/pyload.py)

运行环境在一次运行期间是保存的，你可以先定义函数或者赋值，然后在之后的消息中使用它

最后一行会被作为表达式解析，如果不是`None`，作为文本消息被 bot 发送

异常会被 bot 发送

当最后一行以`#`开头时，视为`None`

特别地，当最后一行以`###`开头时，本次运行的代码文本会被保存进`data/pyload.py`，在 bot 每次启动时加载

在`.py`内使用`print`和`input`会输出和读取聊天区域的消息，但是这不意味着可以在`pyload.py`里这么做

### [.link](/_code/bot/cmds/link.py)(需要权限)

如若 bot 接收到的消息没有触发任何玩意(`^`，阻塞，命令，bash运行)，那么会通过 links 进行处理

links 是 link 构成的列表，每个 link 含有 `name`, `type`, `cond`, `action`, `succ`, `fail` 等键

其中`succ`和`fail`都是 link 的`name`构成的列表

links 的第一个 link 会被作为入口，对传入的消息(不一定是文本消息)进行判断

根据 link 的种类(`type`)，若满足条件(`cond`)则会运行动作(`action`)，然后根据是否满足条件，依次运行`succ`或者`fail`内的其它 links

举个例子，如果想要创建出骰子

```
user:
    .py
    def rd(r,d):
        '''掷骰子'''
        return sum(random.randint(1, d) for _ in range(r))
    ###
bot:
    添加成功
user:
    .link re 骰子
    \.r{r:Int}d{d:Int}$
    ===
    f'{getname()} 投了 {:r} 个 {:d} 面骰，总数为 {rd({:r}, {:d})}'
bot:
    创建成功
user:
    .r1d6
bot:
    user 投了 1 个 6 面骰，总数为 5
```

冒号后面的东西看起来是类型，不过实际上可以是任何变量

一些预设的可以在这里查看: [funcs.py](/_code/funcs.py)

除了自身定义的东西外，能直接调用所有`.py`能直接调用的

顺便，在这个link之前执行的其它link创建的变量也是可以直接用的

实际替换的函数是在这里定义的: [str_tool](/_code/s3/str_tool.py)

在`.py`中可以使用`links`获取 links 列表并进行编辑，不过也可以使用`.link`命令来编辑 links

`.link`和`.py`的运行环境是一致的，这意味着`.link`同样能调用 bot 的几乎全部东西

link 的`type`分为 2 种，分别是`py`和`re`，分别使用`.link py`和`.link re`设置

完整的语法是

```
.link
    : (py|re) <name>[ while[ <other_name> (succ|fail)]+]
        : \\n <cond> \\n===\\n <action>
        | \\n <cond> || <action>
        | || <cond> || <action>
    | get <name>
    | del <name>
    | list
    | catch
        : <text>
        | || <text>
```

设置同名 link 将会覆盖原有的 link

cond 和 action 可以和其它的在一条命令内同时完成设置，也可以分为多条依次输入

while 可以设置它在哪条 link 通过或未通过时执行，当没有 while 参数时，将不会改变原有 link 的 while，否则会覆盖

<details><summary>当使用`py`时</summary>

`cond`和`action`作为 py 代码解析

cond 会以最后一行作为表达式求布尔值作为判断依据，action则是无脑exec

若打算 cond 无条件通过请使用 True 作为最后一行，否则使用 None 或者 以#开头 作为最后一行

action 紧挨着 cond 成功时执行，原则上不允许 conds 使用 send,recv 和 do_action 等干涉自身的函数

</details>

<details><summary>当使用`re`时</summary>

`cond`和`action`分别作为特殊的正则表达式和特殊的 py 代码解析

`action` 的最后一行会像`.py`一样返回，只是不会有`###`的记录

此时`cond`的特殊在于，它是用来生成正则表达式的，生成的正则表达式将捕获的一溜字符串传给`action`，生成 py 代码来运行

捕获的基本原理在于命名组，不过换了个写法

使用类似 python 中 f-string 的形式，而表现起来是这样的 `{name:type}`

当传入新的消息需要被`cond`判断时

`type`会在`.py`的运行环境中寻找同样名字的变量，并转化为字符串

若没有找到或者变量名不合法，则将本身作为字符串

若指向的是列表，则变成`(?:xx|xx|...)`类似这样

若`name`和`type`都有，则以`name`创建命名组，`type`的结果作为命名组的匹配规则

若只有`type`，即`{:type}`，则`type`的结果直接作为字符串插入

若只有`name`，即`{name}`，则根据`name`本身进行判断，当`name`以大写字母开头时插入`[\S\s]+`，否则插入`\S+`

正则表达式若匹配通过则会获取`groupdicts`，包含所有的命名组

替换`action`里所有类似`{:name}`的东西，然后再将`action`作为 py 代码解析

最后一行作为表达式返回

</details>

使用`get`可以得到一个 link 的信息，不包括它能触发哪些 links(这在`list`里有显示)，可以据此便捷重设这个 link

使用 `del` 可以删除 link，连带这个 link 和其它 link 的关系一起

(虽然就算不删应该，，也没啥问题，大概)

使用 `list` 可以列出当前的全部 links 以及它们之间的触发关系

使用 `catch` 可以根据对应的文本，找到这个文本能触发哪些 links，并且终结于哪些 links，debug用

## 额外命令

额外命令是附加的命令，随时可能变动

### [.answer](/_code/bot/cmds/answer.py)

[答案之书](https://www.bing.com/search?q=%E7%AD%94%E6%A1%88%E4%B9%8B%E4%B9%A6)

建议把触发放进links里

```
.link re 答案之书
(柚子(告诉我|我该)怎么办|无所不知伟大的柚子啊，请为我指引吧！|为什么不问问神奇柚子呢|柚子柚子(告诉我)?)
===
getcmd('answer').run('')
```

### [.cave](/_code/bot/cmds/cave.py)

回声洞

格式：

```
.cave [<id:int>]  #获取一条消息
.cave add
 : <msg>    # 放入一条消息
 | || <msg> # 放入一条消息
.cave del [<id:int>] # 删除一条消息，默认为上一条自己放的消息
```

回声洞是全局的，会存储发送者，发送时间和发送群(如果是群聊)

显示的格式为
```
# 于群聊设置
{i}:
{text}
    ——{sender} 于 {group}，
  {time}
```
```
# 于私聊设置
{i}:
{text}
    ——{sender} 于 {time}
```

使用.py的setname和setgroupname可以影响记录在其中的名字和群名

为了让没有权限的人也能修改自己的名字，可以将它设置在link里

```
.link re
叫我{name:.*}
===
setname('{:name}')
'你好，{:name}'
```

### [.jrrp](/_code/bot/cmds/jrrp.py)

今日人品

从0到100之间随机，每人每天只能随机一次

后续再次调用只是返回今天第一次随机出的结果

### [.jrgz](/_code/bot/cmds/jrgz.py)

今日鸽子

仅在群内调用有效，每天可以从群里随机 roll 个人当鸽子

默认不会 at 对方，要加上 at 可以使用 `cq.cq('at',qq=user_id)`

同今日人品一样每天只能随机一次

### [.kmmm](/_code/bot/cmds/kmmm.py)

随机一张 ケモミミちゃん 的作者的作品

### [.setu](/_code/bot/cmds/setu.py)

随机色图，由于真的会把 r18 图片发(不)出来所以注意风控

### [.js](/_code/bot/cmds/js.py)(需要权限)

运行 JavaScript 代码，需要服务器上面安装了 screen 和 node

基于 node 的 [REPL](https://www.bing.com/search?q=REPL)

### [.nim](/_code/bot/cmds/nim.py)(需要权限)

运行 nim 代码，需要服务器上面安装了 nim

基本原理非常简单粗暴

### [.mcf](/_code/bot/cmds/mcf.py)

编辑 mcfunction 文件，需要[指定 Minecraft 文件夹路径](/data/pyload.py)

[服务器管理可以看这里](/md/use/mc.md)

### [.change](/_code/bot/cmds/change.py)

随机起卦 ~~赛博算卦~~