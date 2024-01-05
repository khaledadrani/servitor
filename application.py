from typing import List, Callable

from connection import Connection


def Servitor(*routers: Callable):
    async def asgi_app(scope, receive, send):
        conn = Connection(scope, send=send, receive=receive)
        for router in routers:
            await router(conn)
            if conn.finished:
                return
        if conn.started:
            await conn.finish()
        else:
            conn.resp_status_code = 404
            await conn.send("Not found", finish=True)

    return asgi_app