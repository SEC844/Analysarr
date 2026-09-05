from pydantic import BaseModel


class AuthStatus(BaseModel):
    setup_required: bool
    authenticated: bool


class SetupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CurrentUser(BaseModel):
    username: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
