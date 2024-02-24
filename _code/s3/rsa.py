import rsa



def pub_loads(keytext:str):
    text = keytext.replace('\\n','\n').replace('\\s',' ')
    return rsa.PublicKey.load_pkcs1(text.encode())
def pub_dumps(key:rsa.PublicKey):
    return key.save_pkcs1().decode().replace('\n','\\n').replace(' ','\\s')

def priv_loads(keytext:str):
    return rsa.PrivateKey.load_pkcs1(keytext.encode())
def priv_dumps(key:rsa.PrivateKey):
    return key.save_pkcs1().decode()

# def encrypt(byte, this_priv, that_pub):
#     '''只能加密极为少量的信息'''
#     signature = rsa.sign(byte, this_priv, 'SHA-1')
#     secret = rsa.encrypt(byte, that_pub)
#     return secret+signature

# def decrypt(recv, this_priv, that_pub):
#     '''可能抛出异常: rsa.VerificationError'''
#     secret, signature = recv[:64], recv[64:]
#     byte = rsa.decrypt(secret, this_priv)
#     rsa.verify(byte, signature, that_pub)
#     return byte

