# [添加命令的方法](https://sch246.com/5-yz_bot/1-cmd.html)

## 开始

`_code/bot/cmds`下的非`_`开头的 .py 文件都会被识别成命令，可以使用`.`作为开头进行调用

例如使用`.py`便可以调用`_code/bot/cmds/py.py`定义的命令

以下展示`.echo`命令是如何创建的

---

示例

```
user:
    .echo awa
bot:
    awa
```

新建`echo.py`，注意名字不是`.echo.py`而是`echo.py`，在里面写下这些

```python title="_code/bot/cmds/echo.py"
def run(body:str):
    return body.strip()
```

---

没了，就是这样

`.`开头的命令毫无疑问是文本消息，run 函数接收一个字符串作为参数，如果返回字符串，那么 bot 就会将字符串也作为文本消息返回到这个聊天的区域

这里的 body 是从命令名后面开始算起的字符串，例如直接使用

```
.echo a
```

那么 body 就是` a`，会包含中间的空格

### 基本输入:str

### 基本输出:return

## import

只有本条消息的文本字符串是远远不够的

不过没关系，你可以 import 啊

```python
from main import ...
```

> 外面的 run.py 并不是 bot 真正运行的地方，它只是为了支持`.reboot`命令，bot 直接运行的地方是`_code/main.py`，所以 import 是以它为基准的

cmds 里的命令都是在 bot 马上要运行阶段才被加载的，所以可以毫无顾忌地 import `main`里面的东东

而`main`把`bot`和`s3`模块里的东西几乎 import 了个遍，也不为啥，就为了方便引用

所以四舍五入下就是你可以在任何位置 import 几乎任何东西

### 参数处理:read_params

可以从`main`里 import `read_params`来进行参数处理，它的定义如下

```python
re_read = re.compile(r'\s+(\S+)([\S\s]*)')
re_read_str = re.compile(r'\s+("[^"]*"|\S+)([\S\s]*)')

def read_params(s:str, count=1, read_str=False):
    '''从字符串中读取空白符后的下n段字符串
    如果要读取引号，则可能抛出异常
    读完后剩余的都是空字符串
    若以非空白符开头，抛出SyntaxError
    返回的字符串数量=count+1，最后一个为剩下的部分'''
    if read_str:
        r = re_read_str.match(s)
    else:
        r = re_read.match(s)
    if not r:
        if not s.strip():    # 全是空白符，或者就是一个空字符串
            return ['']*(count+1)
        raise SyntaxError('需要以空白符开头') # 剩下的唯一可能，引号后接非空白符也会触发
    text, last = r.groups()
    if read_str and len(text)>=2 and text[0] in ['"',"'"] and text[0]==text[1]:
        text = text[1:-1]
    if count==1:
        return text, last
    elif count>1:
        return text, *read_params(last, count-1)
    else:
        raise ValueError('count必须大于0')
```

由于命令的触发规则，body要么是空字符串，要么以空白符开头

```python
# 永远可以在开头这么写来读取第一个参数
s, last = read_params(body)
# 读取后面的一个参数
s2, last = read_params(last)
# 读取再后面的3个参数
a, b, c, last = read_params(last, 3)
```

### 关于当前状况的更多信息:[cache](/_code/bot/cache.py)

`from main import cache` ，指`bot.cache`

与`bot.storage`不同，`bot.cache`内的东西会随着bot关闭而丢失

其中的`get`和`set`函数可以用于存取其中的东西

通过`cache.get_last()`，可以获得最后一条被记录的消息字典

通过`cache.thismsg()`可以获取当前线程需要处理的消息字典，它也可以用来设置消息字典

`cache.msgs`存储了bot存储的所有的消息字典，每个聊天区域的消息数不超过256条(默认)，这是它记录消息的核心

通过`cache.getlog(msg)`可以通过一条 msg 获取这个聊天区域的最近消息列表

::: tip

注意，消息列表的存储是最近的放在前面，如果消息列表是lst，那么最近的消息要使用`lst[0]`来获取

:::

`get_one(msg,f,i)`用于在当前聊天区域的最近列表内查找满足条件的最近一条消息，反正别想当历史消息查找用就行了，`f`是一个函数，接收消息字典返回布尔值

[其它的自己看源代码去](/_code/bot/cache.py)

## 等待消息

### 等待消息/特殊输出:yield

如果想要让命令能等待消息或文件啥的，实现类似`input`的功能，使用`yield`

> 由于不想让每个函数加上async这种玩意，我采用手动yield来在单线程内实现异步

其它的已经处理好了）只要`run`函数返回的是个生成器，那么就会被处理

以下展示分条接收参数的命令如何创建

---

示例

```
user:
    .note
bot:
    输入note
user:
    哇
bot:
    记录成功
```

```python title="_code/bot/cmds/note.py"
...
def run(body:str):
    reply = yield '输入note'
    if not is_msg(reply):
        return '不是文本消息，命令终止'
    text = reply['message']
    _save_note(text)
    return '记录成功'
```

---

::: warning

需要注意的是，yield 后，同一个人在同一个地方发送的任何消息，包括`.`开头的命令，都会被阻塞接收作为 reply

同时`yield`也没办法接收另一个人，或者同一个人在另一个地方发送的消息

除非发送`^C`，以`^`开头的消息是唯一比阻塞优先级更高的，这将终止在这个位置的全部阻塞，而命令也不会再继续执行下去

:::
