"""On-demand client for the device-local petpet service."""

import json
import os
import tempfile
from urllib.parse import urlencode

from mods import cq, storage


LOAD_AFTER = ("image", "storage")
petpet_dic = {}


def on_load(_ctx):
    global petpet_dic
    from mods import is_available

    missing = [name for name in ("image", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("petpet 依赖不可用: " + ", ".join(missing))
    petpet_dic = storage.get("", "petpet")


def petpet(**params):
    temporary = None
    avatar = params.get("toAvatar")
    if isinstance(avatar, str) and avatar.startswith(("http://", "https://")):
        try:
            import requests

            response = requests.get(avatar, timeout=10)
            response.raise_for_status()
            suffix = os.path.splitext(avatar.partition("?")[0])[1] or ".img"
            descriptor, temporary = tempfile.mkstemp(prefix="petpet_", suffix=suffix)
            with os.fdopen(descriptor, "wb") as avatar_file:
                avatar_file.write(response.content)
            params["toAvatar"] = f"file://{temporary}"
        except Exception:
            if temporary is not None:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
            temporary = None

    try:
        return cq.url2cq(
            f"http://127.0.0.1:2334/petpet?{urlencode(params)}"
        )
    finally:
        if temporary is not None:
            try:
                os.remove(temporary)
            except OSError:
                pass


def petpet_trans(name: str) -> str:
    return petpet_dic.get(name, name)


def get_petpet_keys() -> list[str]:
    try:
        import requests

        response = requests.get("http://127.0.0.1:2334/petpet", timeout=3)
        response.raise_for_status()
        data = json.loads(response.content.decode("utf-8"))
    except Exception:
        return []
    return [item["key"] for item in data.get("petData", []) if "key" in item]
