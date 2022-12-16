---
title: 真寻bot
tags: [QQ, OneBot]
date: 2022-05-17T23:40
---

import Cover from '@site/src/components/cover/main';

:::caution 这个玩意有一键安装的脚本

虽然这个脚本并不是完全一键安装（至少在 CentOS 8 下大概需要配置py环境（我目前为止还没去尝试过

:::

<!--truncate-->

## 起因

![](https://img1.imgtp.com/2022/05/17/Bks2wI7r.png)

![](https://img1.imgtp.com/2022/05/17/aXHto7uw.png)

## 开始

### 准备QQ号

手机原来可以注册多个QQ的来着，，所以QQ号问题解决了

### 准备服务器

linux服务器，，之前在腾讯云74一年的还没到期，OK

### 查看真寻bot

我去github看看，

![](https://img1.imgtp.com/2022/05/17/0IdzdTns.png)

作者看起来跑路了

先不管有那么多fork，releases还是有留下来的

从fork中找一个备份下来

- [fork](https://github.com/114514huster/zhenxun_bot)

- [README本站备份](/Other/bak/zhenxun_bot)

![](https://img1.imgtp.com/2022/05/18/GsmbaYl2.png)

真寻酱好可爱

进入[文档](https://hibikier.github.io/zhenxun_bot/docs/installation_doc/)，还是挺详细的样子(后来事实证明并不)

![](https://img1.imgtp.com/2022/05/17/YHpTGZPw.png)

### 安装go-cqhttp

[github页面](https://github.com/Mrs4s/go-cqhttp)

[文档页面](https://docs.go-cqhttp.org/)

![](https://img1.imgtp.com/2022/05/18/cjJK0bUi.png)

好可爱

就直接安装可执行文件吧

版本好多

![](https://img1.imgtp.com/2022/05/18/OQw9MV7h.png)

待我百度一下

- [知乎 - x86,x64,x86-64,amd64,arm指令集架构之间的关系](https://zhuanlan.zhihu.com/p/113157931)

![](https://img1.imgtp.com/2022/05/18/vmyNrJle.png)

![](https://img1.imgtp.com/2022/05/18/dHmTNdru.png)

就是linux amd 64了，我选择了名字更短的那个

![](https://img1.imgtp.com/2022/05/18/7MJ1rqkU.png)

里面是这些文件，那个没后缀的就是编译好的linux内的可执行文件

上传上去，按照教程整

![](https://img1.imgtp.com/2022/05/18/dRvmolHK.png)

![](https://img1.imgtp.com/2022/05/18/ZnOCUYPK.png)

<details>

hum，可是我用的CentOS



查看教程

- [腾讯云 - linux下安装ffmpeg的详细教程](https://cloud.tencent.com/developer/article/1711770)

从 http://www.ffmpeg.org/releases/ 下载了最新版本

![](https://img1.imgtp.com/2022/05/18/CLX9kT3b.png)

缺少yasm

我承认搞个bot比我想象的要麻烦，，

除了我下载的ffmpeg是最新版本外一切都顺着腾讯云的教程搞

yasm安装后，安装ffmpeg，它好像在花式警告，也许版本并不是越高越好

不过倒是没有中止

![](https://img1.imgtp.com/2022/05/18/g0mmc4Wz.png)

</details>

试试启动

![](https://img1.imgtp.com/2022/05/18/HQ0LzBla.png)

改密码也不行

换个服务器试试

依旧不行

百度

<details>

<summary>CSDN - 解决xdd/傻妞/go-cqhttp机器人扫码登录异常/全部亲测可用/补充环节【2020年4月30日】</summary>

https://blog.csdn.net/m0_57009761/article/details/124521022

![](https://img1.imgtp.com/2022/05/18/GxLyl9My.png)

</details>

使用最后一种方案成功了

我捋一捋头绪

最后一种方案是信任设备

QQ在信任的设备上登录可以不用扫码

go-cqhttp链接时需要设备信息，若没有则会随机生成一个

若成功登录了，该设备会被QQ信任

设备信息存储在同目录下的`device.json`

因此只要在本地或者随便哪里成功一次制造出一个被QQ信任的设备信息

以该设备信息来连接，就能跳过扫码了

具体操作是把成功那一次使用或生成的`device.json`替换或者复制过去

据测试，需要填入密码才能跳过扫码

一直提示refused，目测需要真寻bot启动才能连上去

done.

### 安装Postgresql数据库

- [菜鸟教程](https://www.runoob.com/postgresql/linux-install-postgresql.html)
- [官网 Linux RedHat 教程](https://www.postgresql.org/download/linux/redhat/)

![](https://img1.imgtp.com/2022/05/18/PW160vWB.png)

全部输入一遍等待许久后

![](https://img1.imgtp.com/2022/05/18/vuWxdako.png)

安装成功了

![](https://img1.imgtp.com/2022/05/18/YgPg5hlh.png)

理论上数据库的语法不会有变化，就直接复制粘贴教程的了

![](https://img1.imgtp.com/2022/05/18/5uTUkSEg.png)

外网连接，，暂时并没有这个需求呢

done.

### 安装真寻Bot

![](https://img1.imgtp.com/2022/05/18/Cg55lcBR.png)

![](https://img1.imgtp.com/2022/05/18/etnUkS3m.png)

符合要求，但是没找到`requirements.txt`

实际上需求的环境在`pyproject.toml`，要使用 Python 的`poetry`库来管理和创建虚拟环境

<details>
<summary>插曲：在上传真寻压缩包的时候浏览README，，</summary>

![](https://img1.imgtp.com/2022/05/18/YVRbA9Ps.png)

![](https://img1.imgtp.com/2022/05/18/odidAe3M.jpg)

反正做了一半了就做到底吧

</details>

![](https://img1.imgtp.com/2022/05/18/uc0UL8av.png)

az

教程开始不适用了

### 尝试一键脚本

第一行`import nonebot`报错

`pip install nonebot`无效

`pip install nb-cli`有改善，变成第二行报错了

显然是环境没有配好

### 尝试Docker

Docker也没了，估计是作者跑路的缘故


##于是第一次的bot之旅到此结束##

## [继续](/md/first.md)