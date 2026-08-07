from pydantic import BaseModel, EmailStr, Field


class SendVerificationCodeRequest(BaseModel):
    email: EmailStr


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=6, max_length=6)


class LoginRequest(BaseModel):
    account: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    tier: str

    class Config:
        from_attributes = True


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
