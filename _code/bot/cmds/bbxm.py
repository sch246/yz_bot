import re
import os

from bot.cmds._bbx import phrase_allowed, source_log_dir


def run(body:str):
    # 正则表达式匹配“我是...猫”，中间不超过16个字符
    pattern1 = re.compile(r'我是(.{0,16})猫')

    # 正则表达式匹配“我是猫”之后不超过9个字符的内容
    pattern2 = re.compile(r'我是猫(.{0,9})')

    # 结果集
    results1 = []
    results2 = []

    try:
        log_dir = source_log_dir()
    except ValueError as e:
        return str(e)

    # 递归遍历配置的群聊日志目录
    for root, dirs, files in os.walk(log_dir):
        for file in files:
            # 拼接完整的文件路径
            path = os.path.join(root, file)
            # 打开并读取文件内容
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                # 找到所有匹配项
                matches1 = pattern1.findall(content)
                matches2 = pattern2.findall(content)
                try:
                    matches1 = [match.strip() for match in matches1 if phrase_allowed(match, 16)]
                    matches2 = [match.strip() for match in matches2 if phrase_allowed(match, 5)]
                except ValueError as e:
                    return str(e)
                # 分别添加到对应的结果集
                results1.extend(matches1)
                results2.extend(matches2)

    # 去重并排序
    unique_results1 = sorted(set(results1))
    unique_results2 = sorted(set(results2))


# 写入结果到文件
    with open('data/bbxm.txt', 'w', encoding='utf-8') as f:
        # 处理“我是...猫”的格式，并替换“我”为“你”
        for match in unique_results1:
            new_line = f'我是{match}猫\n'.replace('我', '你')
            f.write(new_line)
        # 处理“我是猫...”的格式，并替换“我”为“你”，同时确保不会出现重复的“猫”
        for match in unique_results2:
            new_line = f'我是猫{match}\n'.replace('我', '你')
            f.write(new_line)

    return f'百变小猫已更新'
