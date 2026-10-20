"""密码哈希封装，兼容现有 bcrypt 数据并避免旧版 passlib 运行时告警。"""
import bcrypt


class BcryptPasswordContext:
    def hash(self, password: str) -> str:
        raw = password.encode("utf-8")
        if len(raw) > 72:
            raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
        return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False


pwd_context = BcryptPasswordContext()
