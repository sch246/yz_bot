# 柚子bot

[![license](  https://img.shields.io/github/license/sch246/yz_bot)](https://github.com/sch246/yz_bot/blob/main/LICENSE)
[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

柚子bot

远程控制，`.py`运行 python，`.link`映射输入，记录聊天记录

无异步

低性能

暂时没有权限系统(会有的)(划掉)有权限管理系统啦

记录:
- [开始](https://www.sch246.com/Computer/project/qq_bot/start)
- [第一次重构](https://www.sch246.com/Computer/project/qq_bot/yz_bot)
- [第二次重构(就是目前)]

- 0.3版
    - 写着写着就不写了，甚至没有上传github

<details>

<summary>0.4版(目前版本)</summary>

先是用 http 通信发现很方便，于是开始改用 http 通信为基础在写，抛弃了 0.3 版

后来发现似乎不能发送长文本于是改回了 websocket，并封装得比 http 通信的版本还好用

改完了才发现不是 http 的问题，不过管他呢

---

2022/11/6

又改回了 http，因为用 ws 写着确实麻烦）不过因为之前 ws 封装得还不错暂时先留着

</details>



## 内容列表
- [安全性](#安全性)
- [背景](#背景)
- [安装](#安装)
- [使用](#使用)
- [维护者](#维护者)
- [使用许可](#使用许可)

## 安全性

安全性比 0 好一点，但是很有限

有最基本的权限管理系统，你可以理解为类似于我的世界(Minecraft)的 op 系统

除了第一个 op 是 master 且 master 无法被 `.op` 命令删除之外，和 Minecraft 并没有什么区别

op 可以调用 bot 的全部命令和功能，依旧包括 `.py` 这种逆天命令，可以发送命令运行任何 python 代码

因此事实上任何 op 都可以对 master 取而代之，或者对 bot 所在的服务器造成破坏，这点务必注意

## 背景

[真·根源](https://www.sch246.com/blog/2022/05/17/%E7%9C%9F%E5%AF%BBbot)

QQ自带的聊天记录检索不好用是推动我让 bot 能运行的直接原因

用其它的bot总是不那么顺手于是也想要自己写一个

想要通过这个 bot 来思考人的思维模式，我觉得编程语言与自然语言本质应该是一样的，qq bot 似乎能处在这两个的交叉处

然后，能远程重启和编程自己的 bot 超酷的好不

以上这些决定了 柚子bot 具有 `.py`，`.link`，`.reboot` 以及历史记录模块，我也期望 bot 的更多功能实现是可以通过聊天进行远程编辑的，这需要编程语言有比较强大的元编程能力

<details>
<summary>划掉</summary>

有人可能想到了 lisp ，不过我觉得还是 python 更接近伪代码一些(才不是没搞懂怎么用 lisp 和 go-cqhttp 通信)

</details>

满足一般 bot 的功能需求倒是其次，我觉得 柚子bot 的功能应该是一整个对象，所以并**没有插件系统**

当然想用 `.link` 添加一些功能也不是不能做到

## 安装

众所周知(雾)，QQ bot 运行需要2个程序

- 登录QQ，和腾讯的服务器通讯(大概)，是一个qq客户端
- 操作前面的那个程序，处理和回应事件

这里的程序是后一个

关于前一个程序请参考[go-cqhttp](https://github.com/Mrs4s/go-cqhttp)

并且选择正向代理(尽管这意味着 bot 只能同时运行在一个QQ号上)

<details>
<summary>go-cqhttp登录问题</summary>

- [CSDN - 解决xdd/傻妞/go-cqhttp机器人扫码登录异常/全部亲测可用/补充环节【2020年4月30日】](https://blog.csdn.net/m0_57009761/article/details/124521022)

QQ在信任的设备上登录可以不用扫码

go-cqhttp链接时需要设备信息，若没有则会随机生成一个

若成功登录了，该设备会被QQ信任

设备信息存储在同目录下的device.json

因此只要在本地或者随便哪里成功一次制造出一个被QQ信任的设备信息

以该设备信息来连接，就能跳过扫码了

具体操作是在电脑上运行并扫码登录go-cqhttp

把成功那一次使用或生成的device.json替换或者复制过去

据测试，需要填入密码才能跳过扫码(大概)

---

</details>

bot 需要 python 3.10 或更高的版本

需要第三方库 `requests`

然后使用 python 运行`run.py`

第一次启动后会输出类似下面的东西

如果不是第一次启动的话，就只会显示`<你的bot的QQ名>(<你的bot的QQ号>)启动了！`

```
未检测到config，第一次加载中
私聊bot验证码以确定master: 3447
```

这是给 bot 设定一个管理员，用一个有 bot 好友的 qq 对 bot 发送验证码即可

随后的操作和 bot qq 聊天即可

![](https://s2.loli.net/2022/10/18/PgZprRhvBbAG4Yj.png)

## 使用

`!`开头的消息会被识别成命令行指令

`.`开头的消息会被识别成命令，所有的命令在`./bot/cmds`下定义

<details>
<summary>命令</summary>

`.py`可以运行 python 代码，最后一行必须为表达式，或者以`#`开头，或者以`###`开头

`.link`可以检测输入然后进行一些处理(指在`.py`的环境运行 python 代码)

`.echo`会让bot重复echo后的话

`.file`用于查看以及上传下载文件

`.op`可以管理权限

`.reboot`和`.shutdown`可以重启和关闭 bot，在控制台用`Ctrl+C`有时候会没啥反应

`.test`就是个测试命令

输入进来的消息被解析的优先级是

- `^`开头，特殊操作，比如`^C`可以中断 bot 对自身消息的捕获状态
- `.<cmd>`开头，内置命令
- `.link`指定的捕获，没有固定格式，并且可能导致递归
- `!`开头，运行bash

---

</details>

最后一行以`###`开头的 python 代码消息会运行一遍，然后被保存在`./data/pyload.py`，在随后每次加载bot时运行(务必不要依赖非永久的代码，这可能导致bot无法启动)

`.link`虽然不推荐递归(可能导致无限循环卡死bot)，但是依旧可以绕过去

消息记录存储在`./chatlog`下

`./data/cache_msgs`会存储每群或者每人的最近 256 条消息

<details>
<summary>进阶</summary>

新建命令的话，可以在`./bot/cmds`下新建个`<命令名>.py`

在其中定义一个`run`函数

函数的参数是 消息本身的字符串除去开头的`.<命令名>`

函数的返回值是 bot 将要回复的消息

也可以使用`yield <回复:str>`或者`recv(<条件:function>)`来进行多段的交互

`bot.caches.msgs`会存储每群或者每人的最近 256 条消息

</details>

## 维护者

[@sch246](https://github.com/sch246)

## 使用许可

[GPL](LICENSE)