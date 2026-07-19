"""密码哈希与短码生成。

使用 bcrypt 哈希密码；Crockford Base32 生成 6 位易读短码（剔除易混字符）。

bcrypt 72 字节输入限制：bcrypt 只读前 72 字节，超长直接抛 ValueError。
本项目 ``Settings.password_max_length=128`` 允许超 72 字节密码（多字节 UTF-8
字符更易触达上限），采用 SHA256 预哈希标准做法（bcrypt(base64(sha256(pwd)))）——
等长 64 字节摘要，保留 bcrypt 慢哈希抗暴力。OpenBSD 原 bcrypt 实现亦用此法。
"""
import base64
import hashlib
import secrets

import bcrypt


def _prehash(plain: str) -> bytes:
    """bcrypt 72 字节限制规避：SHA256 预哈希后 base64 编码 → 44 字节定长输入。"""
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(plain: str) -> str:
    """哈希明文密码（bcrypt + SHA256 预哈希，rounds=12）。"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_prehash(plain), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与哈希值是否匹配（与 hash_password 同款预哈希）。"""
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except ValueError:
        # 损坏的哈希格式（非 bcrypt / 截断 / 非法 base64）→ 校验失败而非 500
        return False


def generate_short_code() -> str:
    """生成 6 位 Crockford Base32 短码（剔除易混字符）。

    Crockford Base32：0-9 A-Z（剔除 0/O/1/I/L/U）。
    实际可用：23456789ABCDEFGHJKMNPQRSTVWXYZ（32 字符）。
    6 位 = 32^6 ≈ 1B 空间，手输友好。
    """
    alphabet = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(6))
