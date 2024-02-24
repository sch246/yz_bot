# 柚子bot

[![license](  https://img.shields.io/github/license/sch246/yz_bot)](https://github.com/sch246/yz_bot/blob/main/LICENSE)
[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

柚子bot

基于 go-cqhttp 运行

远程控制，`.py`运行 python，`.link`映射输入，记录聊天记录，给mc服务器输入命令

有 GPT 聊天功能，不过需要
- 在 `_code/.env` 内设置 `OPENAI_BASE_URL=".."` 和 `OPENAI_API_KEY=".."`
- 在 `data/storage/chat_groups.json` 里添加允许启用 gpt 的群
    - 或者在聊天使用 `.py chat_groups.append(群号)`

其它命令自行探索

无 async，有一点点多线程

低性能

有权限管理系统啦

不考虑同时运行多个bot，不考虑一个群有多个bot

记录:
- [开始](/md/first.md)
- [第一次重构](/md/second.md)
- [第二次重构(0.2版本)]

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
- [柚子bot](#柚子bot)
  - [内容列表](#内容列表)
  - [安全性](#安全性)
  - [背景](#背景)
  - [安装](#安装)
  - [使用](#使用)
  - [维护者](#维护者)
  - [使用许可](#使用许可)
  - [引用](#引用)

## 安全性

安全性比 0 好一点，但是很有限

有最基本的权限管理系统，你可以理解为类似于我的世界(Minecraft)的 op 系统

除了第一个 op 是 master 且 master 无法被 `.op` 命令删除之外，和 Minecraft 并没有什么区别

op 可以调用 bot 的全部命令和功能，依旧包括 `.py` 这种逆天命令，可以发送命令运行任何 python 代码

因此事实上任何 op 都可以对 master 取而代之，或者对 bot 所在的服务器造成破坏，这点务必注意

## 背景

[真·根源](/md/zhenxun.md)

QQ自带的聊天记录检索不好用是推动我让 bot 能运行的直接原因

用其它的bot总是不那么顺手于是也想要自己写一个

想要通过这个 bot 来思考人的思维模式，我觉得编程语言与自然语言本质应该是一样的，qq bot 似乎能处在这两个的交叉处

然后，能远程重启和编程自己的 bot 超酷的好不

以上这些决定了 柚子bot 具有 `.py`，`.link`，`.reboot` 以及历史记录模块，我也期望 bot 的更多功能实现是可以通过聊天进行远程编辑的，这需要编程语言有比较强大的元编程能力

<details>
<summary>划掉</summary>

有人可能想到了 lisp ，不过我觉得还是 python 更接近伪代码一些(才不是没搞懂怎么用 lisp 和 go-cqhttp 通信)

</details>

bot没有插件系统，不过添加命令本身也是相对独立的，可以当成插件（？）

也可以用 `.link` 添加功能

## 安装

众所周知(雾)，QQ bot 运行需要2个程序

- 登录QQ，和腾讯的服务器通讯(大概)，是一个qq客户端
- 操作前面的那个程序，处理和回应事件

这里的程序是后一个

关于前一个程序请参考[go-cqhttp](https://github.com/Mrs4s/go-cqhttp)

并且选择 http 通信

<details>
<summary>go-cqhttp登录问题(已过期)</summary>

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

需要许多第三方库, 写在 `requirements.txt` 里了

然后运行`python3 run.py`，可以使用`-a`开启自动重启，使用`-l`仅记录聊天记录而不能触发其它功能

第一次启动后会输出类似下面的东西

如果不是第一次启动的话，就只会显示`<你的bot的QQ名>(<你的bot的QQ号>)启动了！`

```
未检测到config，第一次加载中
私聊bot验证码以确定master: 3447
```

这是给 bot 设定一个管理员，用一个有 bot 好友的 qq 对 bot 发送验证码即可

随后的操作和 bot qq 聊天即可

![](https://s2.loli.net/2022/10/18/PgZprRhvBbAG4Yj.png)

## [使用](/md/use.md)

## 维护者

[@sch246](https://github.com/sch246)

## 使用许可

[GPL](LICENSE)

## 引用

- https://github.com/barneygale/MCRcon

[./_code/s3/mcrcon.py](./_code/s3/mcrcon.py)使用了其中的`mcrcon.py`
