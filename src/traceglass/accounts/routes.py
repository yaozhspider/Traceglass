"""Open registration and cookie-session endpoints."""
import asyncio
import re
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .security import COOKIE
from .identity import ensure_analyst


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    username: str = Field(min_length=3,max_length=32,pattern=r'^[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}$')
    password: str = Field(min_length=1,max_length=256)


def create_auth_router(accounts, storage, settings):
    router=APIRouter(prefix='/auth',tags=['accounts'])

    @router.get('/session')
    async def session(request: Request):
        if not settings.auth_enabled:
            return {'auth_enabled':False,'user':None,'csrf_token':None}
        user=getattr(request.state,'account',None)
        return {'auth_enabled':True,'registration':'open',
                'user':{'id':user['id'],'username':user['username']} if user else None,
                'csrf_token':user['csrf'] if user else None}

    async def complete_login(user,request,response):
        await ensure_analyst(storage,user['id'],settings.max_iters)
        old=request.cookies.get(COOKIE)
        if old: await asyncio.to_thread(accounts.logout,old)
        token,csrf=await asyncio.to_thread(accounts.login,user['id'])
        response.set_cookie(COOKIE,token,httponly=True,secure=settings.secure_cookies,samesite='strict',max_age=7*86400,path='/')
        response.headers['Cache-Control']='no-store'
        return {'auth_enabled':True,'user':user,'csrf_token':csrf}

    async def limit(request, name):
        if not settings.auth_enabled: raise HTTPException(404,'当前为本地单用户模式')
        ip=request.client.host if request.client else 'unknown'
        if not await asyncio.to_thread(accounts.limit,'auth:'+ip,30,900):
            raise HTTPException(429,'尝试过于频繁，请稍后重试',headers={'Retry-After':'900'})
        if not await asyncio.to_thread(accounts.limit,'username:'+name.lower(),15,900):
            raise HTTPException(429,'尝试过于频繁，请稍后重试',headers={'Retry-After':'900'})

    @router.post('/register',status_code=201)
    async def register(body:LoginInput,request:Request,response:Response):
        await limit(request,body.username)
        if len(body.password)<12: raise HTTPException(422,'密码至少需要 12 个字符')
        try:
            user=await asyncio.to_thread(accounts.create,body.username,body.password)
        except ValueError:
            raise HTTPException(409,'用户名已存在') from None
        return await complete_login(user,request,response)

    @router.post('/login')
    async def login(body:LoginInput,request:Request,response:Response):
        await limit(request,body.username)
        user=await asyncio.to_thread(accounts.authenticate,body.username,body.password)
        if not user: raise HTTPException(401,'用户名或密码错误')
        return await complete_login(user,request,response)

    @router.post('/logout',status_code=204)
    async def logout(request:Request,response:Response):
        await asyncio.to_thread(accounts.logout,request.cookies.get(COOKIE,''))
        response.delete_cookie(COOKIE,path='/',secure=settings.secure_cookies,httponly=True,samesite='strict')

    return router
