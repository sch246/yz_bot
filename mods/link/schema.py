"""The persisted linear link-node shape."""

import json


FIELDS = {"name", "type", "cond", "action"}


def snapshot(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def validate_import(value) -> list[str]:
    if not isinstance(value, list):
        return [f"期望 JSON 数组，得到 {type(value).__name__}"]
    errors = []
    names = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"[{index}] 不是对象")
            continue
        name = item.get("name", f"[{index}]")
        if name in names:
            errors.append(f"{name}: 名称重复")
        names.add(name)
        if set(item) != FIELDS:
            errors.append(f"{name}: 字段必须是 name/type/cond/action")
        if item.get("type") not in ("re", "py"):
            errors.append(f"{name}: type 无效")
        for key in ("name", "cond", "action"):
            if not isinstance(item.get(key), str):
                errors.append(f"{name}: {key} 应为字符串")
    return errors
