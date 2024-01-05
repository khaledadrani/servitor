from enum import Enum
from functools import cached_property
from http.cookies import SimpleCookie
from typing import Any, Awaitable, Callable, Optional, Union
from urllib.parse import parse_qsl, unquote_plus

from werkzeug.datastructures import Headers, MultiDict

CoroutineFunction = Callable[[Any], Awaitable]


class ConnectionType(Enum):
    HTTP = "HTTP"
    WebSocket = "WebSocket"


class Connection:
    def __init__(
            self, scope: dict, *, send: CoroutineFunction, receive: CoroutineFunction
    ):
        """
        Represents a connection object for handling ASGI communication.

        Parameters:
        - scope: A dictionary containing information about the connection.
        - send: A coroutine function for sending messages to the client.
        - receive: A coroutine function for receiving messages from the client.
        """
        self.scope = scope
        self.asgi_send = send
        self.asgi_receive = receive

        self.started = False
        self.finished = False
        self.resp_headers = Headers()
        self.resp_cookies: SimpleCookie = SimpleCookie()
        self.resp_status_code: Optional[int] = None

        self.http_body = b""
        self.http_has_more_body = True
        self.http_received_body_length = 0

    @cached_property
    def req_headers(self) -> Headers:
        """
        Lazily retrieves and parses request headers.

        Returns:
        - Headers: Parsed request headers.
        """
        headers = Headers()
        for (k, v) in self.scope["headers"]:
            headers.add(k.decode("ascii"), v.decode("ascii"))
        return headers

    @cached_property
    def req_cookies(self) -> SimpleCookie:
        """
        Lazily retrieves and parses request cookies.

        Returns:
        - SimpleCookie: Parsed request cookies.
        """
        cookie = SimpleCookie()
        cookie.load(self.req_headers.get("cookie", {}))
        return cookie

    @cached_property
    def type(self) -> ConnectionType:
        """
        Determines the connection type (HTTP or WebSocket).

        Returns:
        - ConnectionType: Enum representing the connection type.
        """
        return (
            ConnectionType.WebSocket
            if self.scope.get("type") == "websocket"
            else ConnectionType.HTTP
        )

    @cached_property
    def method(self) -> str:
        """
        Retrieves the HTTP method used in the request.

        Returns:
        - str: HTTP method.
        """
        return self.scope["method"]

    @cached_property
    def path(self) -> str:
        """
        Retrieves the requested path.

        Returns:
        - str: Requested path.
        """
        return self.scope["path"]

    @cached_property
    def query(self) -> MultiDict:
        """
        Parses and retrieves the query parameters.

        Returns:
        - MultiDict: Parsed query parameters.
        """
        return MultiDict(parse_qsl(unquote_plus(self.scope["query_string"].decode())))

    async def send(self, data: Union[bytes, str] = b"", finish: Optional[bool] = False):
        """
        Sends a message to the client.

        Parameters:
        - data: Message data to be sent.
        - finish: Indicates whether this is the final message.

        Raises:
        - ValueError: If attempting to send a message when the connection is closed.
        """
        if self.finished:
            raise ValueError("No message can be sent when connection closed")
        if self.type == ConnectionType.HTTP:
            if isinstance(data, str):
                data = data.encode()
            await self._http_send(data, finish=finish)
        else:
            raise NotImplementedError()

    async def _http_send(self, data: bytes = b"", *, finish: bool = False):
        """
        Sends an HTTP response.

        Parameters:
        - data: Response body data.
        - finish: Indicates whether this is the final response.

        Raises:
        - ValueError: If attempting to send a response before starting the response.
        """
        if not self.started:
            if finish:
                self.put_resp_header("content-length", str(len(data)))
            await self.start_resp()
        await self.asgi_send(
            {"type": "http.response.body", "body": data or b"", "more_body": True}
        )
        if finish:
            await self.finish()

    async def finish(self, close_code: Optional[int] = 1000):
        """
        Finishes the connection and sends the final response.

        Parameters:
        - close_code: WebSocket close code (only applicable for WebSocket connections).

        Raises:
        - ValueError: If attempting to finish an already finished connection.
        """
        if self.type == ConnectionType.HTTP:
            if self.finished:
                raise ValueError("Connection already finished")
            if not self.started:
                self.resp_status_code = 204
                await self.start_resp()
            await self.asgi_send(
                {"type": "http.response.body", "body": b"", "more_body": False}
            )
        else:
            raise NotImplementedError()
            # await self.asgi_send({"type": "websocket.close", "code": close_code})
        self.finished = True

    async def start_resp(self):
        """
        Starts the HTTP response by sending the initial response headers.

        Raises:
        - ValueError: If attempting to start the response multiple times.
        """
        if self.started:
            raise ValueError("resp already started")
        if not self.resp_status_code:
            self.resp_status_code = 200
        headers = [
            [k.encode("ascii"), v.encode("ascii")] for k, v in self.resp_headers.items()
        ]
        for value in self.resp_cookies.values():
            headers.append([b"Set-Cookie", value.OutputString().encode("ascii")])
        await self.asgi_send(
            {
                "type": "http.response.start",
                "status": self.resp_status_code,
                "headers": headers,
            }
        )
        self.started = True

    async def body_iter(self):
        """
        Iterates over the request body in chunks.

        Yields:
        - bytes: Chunk of the request body.

        Raises:
        - ValueError: If the connection type is not HTTP or if the body iteration is already started.
        """
        if self.type != ConnectionType.HTTP:
            raise ValueError("connection type is not HTTP")
        if self.http_received_body_length > 0 and self.http_has_more_body:
            raise ValueError("body iter is already started and is not finished")
        if self.http_received_body_length > 0 and not self.http_has_more_body:
            yield self.http_body
        req_body_length = (
            int(self.req_headers.get("content-length", "0"))
            if not self.req_headers.get("transfer-encoding") == "chunked"
            else None
        )
        while self.http_has_more_body:
            if req_body_length and self.http_received_body_length > req_body_length:
                raise ValueError("body is longer than declared")
            message = await self.asgi_receive()
            message_type = message.get("type")
            if message.get("type") == "http.disconnect":
                raise ValueError("Disconnected")
            if message_type != "http.request":
                continue
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise ValueError("Chunk is not bytes")
            self.http_body += chunk
            self.http_has_more_body = message.get("more_body", False) or False
            self.http_received_body_length += len(chunk)
            yield chunk

    async def body(self):
        """
        Reads the entire request body.

        Returns:
        - bytes: Entire request body.
        """
        return b"".join([chunks async for chunks in self.body_iter()])

    def put_resp_header(self, key, value):
        """
        Adds a response header.

        Parameters:
        - key: Header key.
        - value: Header value.
        """
        self.resp_headers.add(key, value)

    def put_resp_cookie(self, key, value):
        """
        Adds a response cookie.

        Parameters:
        - key: Cookie key.
        - value: Cookie value.
        """
        self.resp_cookies[key] = value
