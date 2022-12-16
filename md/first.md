# qq_bot


## 起因

- [blog - 真寻bot](/md/zhenxun.md)


经过一系列的鼓捣还是没能理解并运行后

我通过真寻bot在末尾感谢的

- [bot词库](https://github.com/Kyomotoi/AnimeThesaurus)

找到了

- [ZeroBot-Plugin](https://github.com/FloatTech/ZeroBot-Plugin)
- [ATRI——一个厨力项目](https://github.com/Kyomotoi/ATRI)

其中第一个就算是我也能成功运行最简化版本(稍微复杂的版本遇到了不会处理的错误)

用了一段时间感觉并不是那么顺手，还是想要自己写一个

附带雨弓似乎也想要一个工坊娘

给了我另一个理由

我终于决定开始查询更详细的文档

## 参考

- [知乎 - 教你如何搭建自己的qq机器人，全是干货！](https://zhuanlan.zhihu.com/p/404342876)(并不)
- [Awesome NoneBot](https://awesome.nonebot.dev/)
    - [新-基于Nonebot2和go-cqhttp的机器人搭建](https://yzyyz.top/archives/nb2b1.html)
    - [NoneBot 官网](https://nb2.baka.icu)
    - [github](https://github.com/nonebot/nonebot2)
- [OneBot 官网](https://onebot.dev/)
    - [OneBot11 github](https://github.com/botuniverse/onebot-11)
    - [OneBot12 官网](https://12.onebot.dev)
- [go-cqhttp 官网](https://docs.go-cqhttp.org/)
    - [go-cqhttp github](https://github.com/Mrs4s/go-cqhttp)

## 总览

实现QQ机器人需要两个部件

- 一方输入QQ信息以登录，并且允许交互
- 另一方与前一方进行交互以实现QQ机器人的功能

双方交互的方式由OneBot规定

- OneBot 是一个标准，，规定了QQ登录方与机器人方的通讯方式

- NoneBot 是基于 Python 的对接这个标准的机器人框架

- go-cqhttp 是基于 golang 的用于登录 QQ 并实现这个标准的程序

其中 go-cqhttp 具有很详细的文档（需要在文档页面点击上面的菜单栏进行切换）

NoneBot 文档的内容也很详细，还有几个教程

OneBot的内容似乎不那么重要了）

## OneBot

查看OneBot11的文档，与OneBot12的差别不大

![](https://img1.imgtp.com/2022/05/18/iCqYFkr5.png)

其中api是机器人的输出，事件是机器人的输入

通信和消息，规定了怎么取得联系，怎么交互

![](https://img1.imgtp.com/2022/05/18/i1tcLGII.png)

4种通信模式

如果之前的推论成立

只要满足事件推送和api调用就行了

显然可以看出，WebSocket是双向通讯

所有的通讯都是针对登录qq这一端和机器人这一端之间的

据这里的描述可以推出

OneBot是在qq那一方，NoneBot是机器人那一方

## 我的QQ机器人！

- [腾讯云 - 万字长文，一篇吃透WebSocket：概念、原理、易错常识、动手实践](https://cloud.tencent.com/developer/article/1887095)
- [CSDN - WebSocket - 基于 Python 的主流实现方式总结](https://blog.csdn.net/qq_33961117/article/details/94442908)
    - [简书 - 使用Python Websockets库建立WebSocket客户端链接](https://www.jianshu.com/p/547892eacc7d)
- [websockets官网](https://websockets.readthedocs.io/en/stable/index.html)
- [知乎 - Python Async/Await入门指南](https://zhuanlan.zhihu.com/p/27258289)
- [新-基于Nonebot2和go-cqhttp的机器人搭建](https://yzyyz.top/archives/nb2b1.html)
    - 这个不是必要的，不过懒得折腾的话可以按照这个教程配合插件商店快速整一个好用的QQ机器人出来
    - 如果不是感觉写插件好麻烦我可能就用这个了

注意，自己写一个程序操作 go-cqhttp 的话

要完全根据 go-cqhttp 构建程序，所以查看文档是必要的

- API
    - [go-cqhttp - API](https://docs.go-cqhttp.org/api/)
    - [OneBot11 - API](https://github.com/botuniverse/onebot-11/tree/master/api)
- 事件
    - [go-cqhttp - 事件](https://docs.go-cqhttp.org/event/)
    - [OneBot11 - 事件](https://github.com/botuniverse/onebot-11/tree/master/event)
- [go-cqhttp - CQ Code](https://docs.go-cqhttp.org/cqcode/)

### 设置 go-cqhttp

为了开发方便就在自己电脑上登录了，继续用万能的VSCode

新建文件夹作为工作区，里面再新建一个放 go-cqhttp 的文件夹，随便命名

把下载来的`gocq.exe`文件丢进去(手动重命名一下),`cd ./cqhttp`

启动

```powershell
.\gocq.exe -faststart
```

用默认的设置就行

![](https://img1.imgtp.com/2022/05/19/imafYOyp.png)

再次启动 (我之前登录过了)

![](https://img1.imgtp.com/2022/05/19/1jK3cV3L.png)

由于只开了个正向ws服务器，所以应该不会反复弹出警告

接下来这个账号收到的QQ消息都会显示出来

### 创建python工程

已经开启了 go-cqhttp，且开启了默认的正向ws的情况下

只要用websocket连接这个地址`ws://localhost:6700`就可以接收事件和调用API了

可以连接多个bot到这个地址上面

怎么联系，反正是用websocket，可以看要使用的语言的相关库的教程，我用的是python的websockets

至于格式，在 go-cqhttp 文档里都很清楚

退回到工作区文件夹，再创建一个文件夹作为机器人的文件夹，cd进去

新建一个`.py`文件作为启动机器人的文件，然后参考教程写了个

```python
#!/usr/bin/env python

import asyncio
import websockets
import json

uri = "ws://localhost:6700"

testmsg = json.dumps({
    "action": "send_msg",
    "params": {
        "user_id": "980001119",
        "message": "测试结果"
    }
})


async def recvEvent(websocket, event: dict):
    keys = event.keys()
    if 'post_type' in keys and event['post_type'] == 'message':
        nickname = event['sender']['nickname']
        user_id = event['user_id']
        raw_message = event['raw_message']
        message_id = event['message_id']
        if event['message_type'] == "private":
            print(f"私聊> {nickname}({user_id}): {raw_message} ({message_id})")
        elif event['message_type'] == "group":
            group_id = event['group_id']
            print(f"群聊> {group_id} | {nickname}({user_id}): {raw_message} ({message_id})")
        if event['message'] == '.测试':
            # 收到'.测试'的消息就发送testmsg，即对我私聊'测试结果'
            await websocket.send(testmsg)
    elif 'meta_event_type' in keys and event['meta_event_type'] == 'heartbeat':
        # 这里是每3秒发送的心跳事件，无视了
        return
    elif 'status' in keys:
        # 这里是发送信息后返回是否发送成功什么的事件，无视了
        return
    else:
        print(f"其它> {event}")

async def main():
    while True:
        print('尝试连接.. ')
        try:
            async with websockets.connect(uri) as websocket:
                try:
                    async for event in websocket:
                        # 持续接收事件直到断开
                        await recvEvent(websocket,  json.loads(event))
                except websockets.ConnectionClosed:
                    print('连接关闭', end=' > ')
                    continue
        except ConnectionRefusedError:
            print('连接被拒绝', end=' > ')
            continue

if __name__ == "__main__":
    asyncio.run(main())

```

![](https://img1.imgtp.com/2022/05/20/ejSXIwIE.png)

![](https://img1.imgtp.com/2022/05/20/4hxbRjgy.png)

看看返回值是什么样

```python
if 'post_type' in keys and event['post_type'] == 'message':
    ...
    if event['message_type'] == "private":
        ...
        if event['raw_message'] == 'test':
            print('test')
            task1 = asyncio.create_task(Bot.websocket.send(testmsg))
            print(await task1)
```
```python title='result'
私聊> 康康(980001119): test (1981975747)
test
None
```

::: details QAQ

### 处理事件

> 事件是用户需要从 OneBot 被动接收的数据，有以下几个大类：
> 
> - 消息事件，包括私聊消息、群消息等
> - 通知事件，包括群成员变动、好友变动等
> - 请求事件，包括加群请求、加好友请求等
> - 元事件，包括 OneBot 生命周期、心跳等
> 
> 在所有能够推送事件的通信方式中（HTTP POST、正向和反向 WebSocket），事件都以 JSON 格式表示。

- 其中我打算把消息事件分为命令和普通消息，命令必须回复，而普通消息可以不回复

- 而消息又分为群消息和私聊消息

- 群消息可能还有at自身的消息

- 对其它事件作出反应

以及额外的输入

- QQ机器人要能定期执行任务

- 知道自己刚刚说了什么或者做了什么(如果发送成功的话就记录，发送失败触发其它事件)

    - 短时间自身说太多的话也可以作为输入阻止机器人说太快

于是我想做个中间层，类似命令行一样的东东

命令消息可以直接调用这里的命令行

命令行可以模拟调用全部的输入和输出，并且执行python语句, python的exec函数可以做到

命令行可以设置事件的绑定，设置事件执行的函数以及修改设置

但是即使能执行各种语句和修改，也只有特定的东西才能被保存，中途修改的类什么的是不能被保存的，，残念

:::

## 学习协程

想让api调用能直接返回值，不然每次都要等主循环也太麻烦了

没搞懂协程始终没法搞

于是不得不学协程了）

<!-- [blog - 学习python协程](/blog/2022/05/25/%E5%AD%A6%E4%B9%A0python%E5%8D%8F%E7%A8%8B) -->
