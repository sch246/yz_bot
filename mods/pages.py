"""Small generator-based pagination helpers."""

from mods import msgs


def paginate(content, page_size=10, current_page=1):
    """Return ``(content, total_pages, current_page, prompt)`` for one page."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if isinstance(content, str):
        items = content.strip().split("\n") if content.strip() else []
        is_text = True
    elif isinstance(content, list):
        items = content
        is_text = False
    else:
        raise TypeError("content必须是字符串或列表")

    total_pages = (len(items) + page_size - 1) // page_size
    if total_pages == 0:
        current_page = 1
        page = []
    else:
        current_page = max(1, min(current_page, total_pages))
        start = (current_page - 1) * page_size
        page = items[start : start + page_size]

    if is_text:
        page = "\n".join(page)
    prompt = f"------ 第 {current_page}/{total_pages} 页 ------\n"
    return page, total_pages, current_page, prompt


def display(content, page_size=10, init_page=1):
    """Yield pages and consume navigation replies from the same interaction line."""
    current_page = init_page
    page, total_pages, current_page, prompt = paginate(
        content, page_size, current_page
    )
    if total_pages == 0:
        return "内容为空！"

    shown = "\n".join(map(str, page)) if isinstance(page, list) else page
    if total_pages == 1:
        return shown

    reply = yield f"{shown}\n\n{prompt}"
    last_invalid = False
    while True:
        if not msgs.is_msg(reply):
            return "非消息，翻页终止"
        value = msgs.body(reply).strip().lower()
        if value in ("q", "quit", "exit", "退出"):
            return "翻页已结束"
        if value in ("n", "next", "下一页"):
            if current_page >= total_pages:
                reply = yield "已经是最后一页了！"
                last_invalid = False
                continue
            current_page += 1
        elif value in ("p", "prev", "上一页"):
            if current_page <= 1:
                reply = yield "已经是第一页了！"
                last_invalid = False
                continue
            current_page -= 1
        elif value.isdigit():
            target = int(value)
            if not 1 <= target <= total_pages:
                reply = yield f"页码超出范围！请输入1-{total_pages}之间的数字"
                last_invalid = False
                continue
            current_page = target
        else:
            if last_invalid:
                return "翻页已结束"
            reply = yield (
                "回复 p/prev 查看上一页，n/next 查看下一页，数字跳转到指定页，"
                "q/quit 退出"
            )
            last_invalid = True
            continue

        page, total_pages, current_page, prompt = paginate(
            content, page_size, current_page
        )
        shown = "\n".join(map(str, page)) if isinstance(page, list) else page
        reply = yield f"{shown}\n\n{prompt}"
        last_invalid = False


def prints(content, page_size=10, init_page=1):
    """Display pages from dynamic Python using its chat ``input``/``print`` pair."""
    from mods import py

    gen = display(content, page_size=page_size, init_page=init_page)
    reply = None
    try:
        while True:
            prompt = gen.send(reply)
            reply = {"message": py.chat_input(prompt)}
    except StopIteration as stop:
        py.chat_print(stop.value)
