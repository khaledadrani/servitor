# routing.py
import functools
from typing import Callable, Iterable, Optional

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, RequestRedirect, Rule

from connection import Connection
from response import HttpResponse

class Router:
    def __init__(self):
        super().__init__()
        self.url_map = Map()
        self.endpoint_to_handler = {}

    def route(self, rule, methods=None, name=None):
        methods = set(methods) if methods is not None else None
        if methods and "OPTIONS" not in methods:
            methods.add("OPTIONS")

        def decorator(name: Optional[str], handler: Callable):
            self.add_route(
                rule_string=rule, handler=handler, methods=methods, name=name
            )
            return handler

        return functools.partial(decorator, name)

    def add_route(
        self,
        *,
        rule_string: str,
        handler: Callable,
        name: Optional[str] = None,
        methods: Optional[Iterable[str]] = None,
    ):
        if not name:
            name = handler.__name__
        existing_handler = self.endpoint_to_handler.get(name)
        if existing_handler and existing_handler is not handler:
            raise ValueError("Duplicated route name: %s" % (name))
        self.url_map.add(Rule(rule_string, endpoint=name, methods=methods))
        self.endpoint_to_handler[name] = handler

    def get_url_binding_for_connection(self, connection: Connection):
        scope = connection.scope
        return self.url_map.bind(
            connection.req_headers.get("host"),
            path_info=scope.get("path"),
            script_name=scope.get("root_path") or None,
            url_scheme=scope.get("scheme"),
            query_args=scope.get("query_string", b""),
        )

    async def __call__(self, connection: Connection):
        try:
            rule, args = self.get_url_binding_for_connection(connection).match(
                return_rule=True, method=connection.scope.get("method")
            )
        except RequestRedirect as e:
            connection.resp_status_code = 302
            connection.put_resp_header("location", e.new_url)
            return await connection.send(f"redirecting to: {e.new_url}", finish=True)
        except MethodNotAllowed:
            connection.resp_status_code = 405
            return await connection.send(b"", finish=True)
        except NotFound:
            return await connection.send(b"Not Found", finish=True)
        else:
            handler = self.endpoint_to_handler[rule.endpoint]
            res = await handler(connection, **args)
            if isinstance(res, HttpResponse):
                res.connection = connection
                await res

