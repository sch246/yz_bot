# 柚子bot

[![license](  https://img.shields.io/github/license/sch246/yz_bot)](https://github.com/sch246/yz_bot/blob/main/LICENSE)
[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

柚子bot

远程控制，`.py`运行 python，`.link`映射输入，记录聊天记录

无异步，低性能，暂时没有权限系统(会有的)

记录:
- [开始](https://www.sch246.com/Computer/project/qq_bot/start)
- [第一次重构](https://www.sch246.com/Computer/project/qq_bot/yz_bot)
- [第二次重构(暂无)]

## 内容列表
- [安全性](#安全性)
- [背景](#背景)
- [安装](#安装)
- [使用](#使用)
- [维护者](#维护者)
- [使用许可](#使用许可)

## 安全性

目前没有权限系统，而且有`.py`这种逆天命令

安全性基本是0

任何加了 bot 好友的人，或者 bot 所在群的任何人都可以发送命令运行任何 python 代码

不要用 root 用户启动 bot 吧

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

当然想用 `.link` 添加一些功能也不是不能做到()

## 安装

众所周知(雾)，QQ bot 运行需要2个程序，一个用来登录QQ，一个用来处理和回应事件，这里的程序是后一个

关于前一个程序请参考[go-cqhttp](https://github.com/Mrs4s/go-cqhttp)，并且选择正向代理(尽管这意味着 bot 只能同时运行在一个QQ号上)

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

据测试，需要填入密码才能跳过扫码

---

</details>

bot 需要 python 3.7.9 或更高的版本

更低的版本不知道能不能运行，反正我没试过

需要第三方库 `websockets` 和 `dill`

然后使用 python 运行`run.py`

成功启动后大概会输出这个

```
尝试连接..
initfunc> 运行初始化函数
正在获取登录信息..
正在获取好友列表..
正在获取群列表..
initfunc> 初始化完成
其它>{'_post_method': 2, 'meta_event_type': 'lifecycle', 'post_type': 'meta_event', 'self_id': <你的QQ号>, 'sub_type': 'connect', 'time': ....}
好友列表获取完成,共xx个
<你的QQ的昵称> (<你的QQ号>), 已启动
群列表获取完成,共xx个
```

## 使用

启动成功后对 bot 发特定信息应该能有反应了

发送`.help`可以查看帮助，详细的可以自己看

几个核心命令在这里有说:https://www.sch246.com/Computer/project/qq_bot/yz_bot

`.py`内的运行环境存储在`run.py`同目录的 `./storage/msg_locals.pkl` 内，你可以手动保存，也可以删掉它来回复原始的环境

聊天记录存在同目录的`./log/`文件夹内

设置存在同目录的`Config.json`内，应该挺好理解的

以上这些运行一次后会自动生成，不用手动创建

## 维护者

[@sch246](https://github.com/sch246)

### Any optional sections

## 使用许可

[GPL](LICENSE)