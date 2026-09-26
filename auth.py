"""Auth disabled: single-user local mode.

历史上是 cookie session 登录；现在所有请求自动以默认管理员身份通过，
历史数据（角色/消息/记忆都挂在 admin 名下）无缝沿用。
"""
from __future__ import annotations

from fastapi import Depends, Request

from config import get_settings


SESSION_UID_KEY = "uid"


def current_user(request: Request) -> str:
    # 免登录：固定返回默认用户名，历史数据继续挂在它名下
    return get_settings().admin_username


CurrentUser = Depends(current_user)