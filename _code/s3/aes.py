from Crypto.Cipher import AES
from binascii import b2a_hex, a2b_hex


# 如果text不足16位的倍数就用空格补足为16位
def add_to_16(text:bytes|str,is_str=True)->bytes:
    if is_str:
        text = text.encode()
    if len(text) % 16:
        add = 16 - (len(text) % 16)
    else:
        add = 0
    text = text + (b'\0' * add)
    return text


# 加密函数
def encrypt(text:str|bytes, key:bytes, iv:bytes,is_str=True):
    mode = AES.MODE_CBC
    text = add_to_16(text,is_str)
    cryptos = AES.new(key, mode, iv)
    cipher_text = cryptos.encrypt(text)
    # 因为AES加密后的字符串不一定是ascii字符集的，输出保存可能存在问题，所以这里转为16进制字符串
    return b2a_hex(cipher_text)


# 解密后，去掉补足的空格用strip() 去掉
def decrypt(byte:bytes, key:bytes, iv:bytes,is_str=True) -> bytes|str:
    mode = AES.MODE_CBC
    cryptos = AES.new(key, mode, iv)
    result = cryptos.decrypt(a2b_hex(byte)).rstrip(b'\0')
    if is_str:
        return result.decode()
    else:
        return result


if __name__ == '__main__':
    from os import urandom
    key, iv = urandom(16), urandom(16)
    e = encrypt("r", key, iv)  # 加密
    d = decrypt(e, key, iv)  # 解密
    print("加密:", e)
    print("解密:", d)
    e = encrypt(b"awa", key, iv,is_str=False)  # 加密
    d = decrypt(e, key, iv,is_str=False)  # 解密
    print("加密:", e)
    print("解密:", d)
