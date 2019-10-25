import logging
import traceback

import asks
import async_property
import trio
import trio_websocket
from asks.response_objects import Response

from quant.common import messages, asks_socks5

from quant.aclient.handler_transit import TransitConnectionHandler
from quant.aclient import state


logger = logging.getLogger(__name__)


class ControlConnectionHandler(object):

    def __init__(self, id: str, base_url: str):
        self.id = id
        self.base_url = base_url
        self.nursery = None
        self.ws: trio_websocket.WebSocketConnection = None
        self.active = True

        self.transit_connection_handler: TransitConnectionHandler = None

        self.logger = logging.getLogger(f"CCH")

    @property
    def connect_url(self):
        return f"{self.base_url.replace('http:', 'ws:').replace('https:', 'wss:')}/connect"

    @async_property.async_property
    async def countries(self):
        resp: Response = await asks.get(f"{self.base_url}/countries")
        return resp.json()

    async def stop(self):
        self.active = False

    async def handle(self):
        async with trio.open_nursery() as nursery:
            self.nursery = nursery

            endpoint = await self.get_endpoint()

            while self.active:
                try:
                    logger.debug(f"connecting ....")
                    ws = await trio_websocket.connect_websocket_url(nursery, url=self.connect_url)
                    self.ws = ws
                    if ws:
                        await self.login()
                        await self.activate_endpoint(endpoint)
                        await self.handle_control_connection()
                except Exception as e:
                    logger.debug(f"E: {e} {traceback.format_exc()}")
                    await trio.sleep(3)

    async def login(self):
        auth_msg = messages.MessageConnect(id=self.id)
        await self.ws.send_message(auth_msg.dump())

    async def activate_endpoint(self, endpoint):
        await self.ws.send_message(messages.MessageActivate(endpoint=endpoint).dump())

    async def get_endpoint(self):

        country_first = (await self.countries)[0]
        endpoints = (await asks.get(f"{self.base_url}/endpoints/{country_first}")).json()
        endpoint = endpoints[0]
        logger.debug(f"selected endpoint = {endpoint}")
        return list(endpoint.keys())[0]

    async def handle_control_messages(self):
        while True:
            try:
                msg = messages.load_js_message(await self.ws.get_message())
                if isinstance(msg, messages.MessageActivateResult):
                    await self.handle_activate_result(msg)
                elif isinstance(msg, messages.MessageCredentials):
                    await self.handle_credentials(msg)
                elif isinstance(msg, messages.MessageActivateTransitEndpoint):
                    await self.handle_activate_transit_endpoint(msg)
                else:
                    await self.ws.aclose()
                    raise RuntimeError(f"unknown msg type: {msg}")
            except Exception as e:
                logger.debug(f"ex {e} {traceback.format_exc()}")
                break

    async def handle_control_connection(self):
        self.nursery.start_soon(self.handle_control_messages)
        await trio.sleep(1)
        while True:
            try:
                await self.ws.send_message(messages.MessageAlive(id=self.id).dump())
            except Exception as e:
                break
            finally:
                await trio.sleep(30)

    # noinspection PyMethodMayBeStatic
    async def set_credentials(self, user, password, host, port):
        state.socks_host = host
        state.socks_port = port
        state.socks_user = user
        state.socks_pass = password

    async def handle_activate_result(self, msg: messages.MessageActivateResult):
        status = msg.status
        reason = msg.reason

        if status == messages.ActivateStatus.SUCCESS:
            c = msg.credentials
            s = c['socks5']
            u = c['udp']
            await self.set_credentials(s['user'], s['password'], s['host'], s['port'])
        else:
            self.logger.warning(f"cannot activate endpoint: {reason}")

    async def handle_credentials(self, msg: messages.MessageCredentials):
        c = msg.credentials
        s = c['socks5']
        u = c['udp']
        await self.set_credentials(s['user'], s['password'], s['host'], s['port'])

    async def start_new_transit_connection_handler(self, token, host, port):
        if self.transit_connection_handler:
            await self.transit_connection_handler.stop()
        self.transit_connection_handler = TransitConnectionHandler(token, host, port)
        self.nursery.start_soon(self.transit_connection_handler.handle)

    async def handle_activate_transit_endpoint(self, msg: messages.MessageActivateTransitEndpoint):
        token = msg.token
        host = msg.host
        port = msg.port
        await self.start_new_transit_connection_handler(token, host, port)
