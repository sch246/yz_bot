"""Pure JSON projection encoding."""

import hashlib
import json


def serialize(value, on_drop=None) -> tuple[str, str]:
    """Render the disk projection and the change-detection digest.

    WHY: ``default`` 把无法 JSON 化的值写成 ``null``、``skipkeys`` 丢掉非字符串键，
    都是为了不让一个坏值卡住整次保存——这个目标成立。但代价是静默的数据损坏：``.py``
    往 storage 塞进一个对象，重启后它就是 ``null``，而 digest 也是从这个有损结果算的，
    所以内存与磁盘"看起来一致"，同步逻辑永远不会报警。
    ``on_drop`` 就是为此存在的：真正落盘时把丢掉的东西报出来。行为不变，只是不再无声。
    仍然无声的是 ``skipkeys``——非字符串键没有对应钩子，要发现它得深走整个结构，
    目前不值得；这是已知的剩余缺口。
    """
    def _default(obj):
        if on_drop is not None:
            on_drop(obj)
        return None

    text = json.dumps(
        value,
        indent=4,
        ensure_ascii=False,
        skipkeys=True,
        default=_default,
    )
    # WHY: 文件写的是 indent=4 的好看形式，digest 却从排序、紧凑的规范形式算。
    # 于是手改文件时只重排键或改缩进不算"变化"，不会触发一轮无意义的回写，也不会被
    # 当成与内存冲突。变化检测比的是内容，不是字节。
    normalized = json.dumps(
        json.loads(text),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text, hashlib.sha256(normalized.encode()).hexdigest()


def digest(value) -> str:
    return serialize(value)[1]


def read_file(path: str, delete_marker: str = "DELETE") -> tuple[str, object | None]:
    """Read one projection file, including its two hand-editing conventions.

    WHY: 这两种"魔法内容"是有意设计的，为的是让直接编辑 data/storage/*.json 成为一种
    真正可操作的维护手段，而不是只能改改字段值：
    - 文件内容写 ``DELETE``：删掉这一项的内存与文件。手上只有一个编辑器时，这是删除
      一整项的办法。
    - 文件内容为空：从内存回写重建。这是"改坏了、还原不回来"的退路——把文件清空，
      下一轮同步就按内存里的样子重新写一份出来。
    两者都是用户可见行为，记在 docs/runtime.md 的「手工编辑 storage 文件」一节。
    """
    with open(path, encoding="utf-8") as file:
        text = file.read()
    stripped = text.strip()
    if not stripped:
        return "empty", None
    if stripped == delete_marker:
        return "delete", None
    return "json", json.loads(text)
