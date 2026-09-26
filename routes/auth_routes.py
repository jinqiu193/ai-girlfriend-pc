"""Auth routes: /login (GET form + POST submit), /logout."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import login, logout

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if login(request, username, password):
        return RedirectResponse(url="/chat", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "用户名或密码不正确"}, status_code=401
    )


@router.post("/logout")
def logout_route(request: Request):
    logout(request)
    return RedirectResponse(url="/login", status_code=303)