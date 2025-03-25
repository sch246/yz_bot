from main import is_msg

def paginate(content, page_size=10, current_page=1):
    """分页控制函数

    参数:
        content: 可以是多行字符串或列表
        page_size: 每页显示的行数/元素数
        current_page: 当前页码（从1开始）

    返回:
        (当前页内容, 总页数, 当前页码, 分页提示)
    """
    # 处理多行字符串情况
    if isinstance(content, str):
        lines = content.strip().split('\n')
    # 处理列表情况
    elif isinstance(content, list):
        lines = content
    else:
        raise TypeError("content必须是字符串或列表")

    # 计算总页数
    total_pages = (len(lines) + page_size - 1) // page_size

    # 确保当前页码在有效范围内
    current_page = max(1, min(current_page, total_pages))

    # 计算当前页的内容范围
    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, len(lines))

    # 获取当前页内容
    current_content = lines[start_idx:end_idx]

    # 如果是字符串情况，将行列表转回字符串
    if isinstance(content, str):
        current_content = '\n'.join(current_content)

    # 生成分页提示
    nav_prompt = f"------ 第 {current_page}/{total_pages} 页 ------\n"
    # nav_prompt += "回复 p/prev 查看上一页，n/next 查看下一页，数字跳转到指定页，q/quit 退出"

    return current_content, total_pages, current_page, nav_prompt

def display(content, page_size=10):
    """翻页展示内容

    使用yield等待用户输入来控制翻页

    参数:
        content: 多行字符串或列表
        page_size: 每页显示的行数/元素数

    用法:
        response = yield from display(content, page_size)
        return response # 当用户退出翻页时的最终响应
    """
    current_page = 1
    total_pages = 0

    # 获取当前页内容和导航信息
    page_content, total_pages, current_page, nav_prompt = paginate(content, page_size, current_page)

    # 显示当前页内容和导航提示
    if isinstance(page_content, list):
        display = '\n'.join(page_content)
    else:
        display = page_content

    if total_pages == 1:
        return display

    # 发送当前页和导航提示
    response = yield f"{display}\n\n{nav_prompt}"

    while True:

        # 检查用户输入是否为消息
        if not is_msg(response):
            return "非消息，翻页终止"

        user_input = response['message'].strip().lower()

        # 处理用户输入
        if user_input in ['q', 'quit', 'exit', '退出']:
            return "翻页已结束"
        elif user_input in ['n', 'next', '下一页']:
            if current_page < total_pages:
                current_page += 1
            else:
                response = yield "已经是最后一页了！"
                continue
        elif user_input in ['p', 'prev', '上一页']:
            if current_page > 1:
                current_page -= 1
            else:
                response = yield "已经是第一页了！"
                continue
        elif user_input.isdigit():
            page_num = int(user_input)
            if 1 <= page_num <= total_pages:
                current_page = page_num
            else:
                yield f"页码超出范围！请输入1-{total_pages}之间的数字"
        else:
            response = yield "回复 p/prev 查看上一页，n/next 查看下一页，数字跳转到指定页，q/quit 退出"
            continue

        # 获取当前页内容和导航信息
        page_content, total_pages, current_page, nav_prompt = paginate(content, page_size, current_page)

        # 显示当前页内容和导航提示
        if isinstance(page_content, list):
            display = '\n'.join(page_content)
        else:
            display = page_content

        # 发送当前页和导航提示
        response = yield f"{display}\n\n{nav_prompt}"