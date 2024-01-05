from uvicorn import Server, Config

from application import Servitor
from connection import Connection
from response import JsonResponse
from routing import Router


async def app(scope, receive, send):
    conn = Connection(scope, receive=receive, send=send)
    name = conn.query.get("name")
    await conn.send("Hello, " + (name or "world"), finish=True)


async def app(scope, receive, send):
    conn = Connection(scope, receive=receive, send=send)
    await JsonResponse(conn.query.to_dict(flat=False), conn)


router = Router()


@router.route("/hello/<name>")
async def hello(connection, name):
    return JsonResponse({'hello': name})


async def app(scope, receive, send):
    conn = Connection(scope, receive=receive, send=send)
    await router(conn)


app = Servitor(router)

if __name__ == "__main__":
    Server(Config(app=app, host="localhost", port=8044)).run()
