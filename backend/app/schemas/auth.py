from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    totp_code: str | None = Field(None, min_length=6, max_length=6)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserInfo"
    requires_2fa: bool = False


class TwoFALoginRequired(BaseModel):
    requires_2fa: bool = True
    temp_token: str
    message: str = "请输入两步验证码"


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    avatar: str
    totp_enabled: bool
    google_email: str | None = None
    permissions: list[str] = []

    class Config:
        from_attributes = True


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=6, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class Setup2FAResponse(BaseModel):
    secret: str
    qr_uri: str


class Verify2FARequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(..., min_length=10)


class BindGoogleEmailRequest(BaseModel):
    google_email: str = Field(..., min_length=5, max_length=100)


class UnbindGoogleEmailRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6)


class MessageResponse(BaseModel):
    message: str
