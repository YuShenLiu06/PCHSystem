"""密码哈希与短码生成测试（AAA 结构）。"""
import string

import pytest

from app.core.security import generate_short_code, hash_password, verify_password


@pytest.mark.parametrize(
    "plain",
    ["short", "中文字符", "12345678", "!@#$%^&*()", "a" * 128],
)
def test_hash_and_verify_password(plain):
    # Arrange
    hashed = hash_password(plain)

    # Act & Assert
    assert hashed != plain  # 哈希值不等明文
    assert hashed.startswith("$2b$12$") or hashed.startswith("$2b$")  # bcrypt 前缀
    assert verify_password(plain, hashed) is True


def test_verify_wrong_password():
    # Arrange
    hashed = hash_password("correct")

    # Act & Assert
    assert verify_password("wrong", hashed) is False


def test_generate_short_code_length_and_charset():
    # Act
    code = generate_short_code()

    # Assert
    assert len(code) == 6
    allowed = set("23456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert all(c in allowed for c in code), f"短码含易混字符: {code}"
    # 易混字符集（应被剔除）
    ambiguous = set("01OILU")
    assert not any(c in ambiguous for c in code), f"短码含易混字符: {code}"


def test_generate_short_code_uniqueness():
    # Act & Assert（生成 100 个短码，重复概率应极低）
    codes = {generate_short_code() for _ in range(100)}
    assert len(codes) > 95  # 允许极少量碰撞，但 >95% 应唯一
