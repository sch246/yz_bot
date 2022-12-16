# [MC服务器终端](https://sch246.com/5-yz_bot/2-mc.html)

柚子bot 可以作为 Minecraft 服务器的终端使用

## 设置

其具体配置需要在`data/pyload.py`中设置

```python
# mc启动函数，配置好后只需要无脑 start_mc() 和 stop_mc() 就行了

# rcon连接设置
rcon_address = '0.0.0.0'
rcon_port = 25575
rcon_password = '123456'

mc_screen = 'mc'            # 想要mc在名叫什么的screen里运行
mc_path = '/opt/mc1192'     # mc的路径
mc_startbash = './run.sh'   # 相对于mc的路径，它的启动脚本在哪里
mc_worldname = 'world'
mc_packformat = 6
```

为了能读取 screen 的输出，需要让 bot 启动对应的 screen 和 mc服务端

需要在服务器的配置中开启 rcon 才能使用完整的功能，虽然不开也不是不能用就是）

## 启动

配置好后在`.py`里使用`start_mc()` `stop_mc()`等命令就能启动和关闭 mc 了

启动过程中会输出大量的日志）

## 管理

在`.py`中使用`mc.send(<command:str>)`或者`screen.send(mc_screen, <command:str>)`来发送命令

两者的区别在于一个是通过 rcon，一个是通过 screen

rcon 的返回会更快，而 screen 的返回可能会延迟好几秒(在0.4.2解决该问题)，且可能带有多余的输出(一般没有)

但好处是 screen 可以获取到`say`这种命令的输出

~~我寻思这么麻烦不如写个mod~~

bot 重启会断开与 mc 的 rcon 的连接，再次使用`start_mc()`就行，挺无脑的

也可以在`pyload.py`里加上`connect_mc()`，这样每次启动都能自动连接了

如果不想输入命令太麻烦的话，可以用`.link`设置一下

```
.link re mc命令
\{command:.*}
===
mc.send('{:command}')
```

```
.link re mc命令2
\\{command:.*}
===
screen.send('mc','{:command}')
```

务必注意先后顺序，后设置的命令默认来说优先级更高，请让`\\`被检测的优先级更高，否则`\\`将没有机会被触发