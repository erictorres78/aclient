import logging
import typing
import traceback

import trio

from quant.common import messages
from quant.common import socks5


class TransitConnectionHandler(object):

    def __init__(self, token, host, port):
        self.active = True
        self.logger = logging.getLogger(self.__class__.__name__)
        self.token = token
        self.host = host
        self.port = port
        self.conn: trio.abc.Stream = None
        self.nursery = None
        self.opened_streams: typing.Dict[int, trio.abc.Stream] = dict()

    async def stop(self):
        self.active = False
        if self.ws:
            await self.ws.aclose()

    async def handle(self):
        self.logger.debug(f"handle called")
        while self.active:
            try:
                await self.handle_transit_connection()
            except Exception as e:
                active = self.active
                self.logger.debug(f"ex {e} {traceback.format_exc()}")
                if not active:
                    break
                await trio.sleep(5)

    async def handle_transit_connection(self):
        self.logger.debug(f"starting to handle transit connection.... to {self.host}:{self.port} with {self.token}")
        conn = await trio.open_tcp_stream(self.host, self.port)
        if conn:
            self.conn = conn
            connect_message_to_ts = messages.TransitEndpointConnection(self.token)
            await messages.write_to_server_stream(self.conn, connect_message_to_ts)

            async with trio.open_nursery() as nursery:
                self.nursery = nursery
                nursery.start_soon(self.transit_connection_reader)
        else:
            await trio.sleep(5)

    async def transit_connection_reader(self):
        while True:
            msg = await messages.read_from_server_stream(self.conn)

            if isinstance(msg, messages.DataChunk):
                await self.handle_request_data_chunk(msg)
            elif isinstance(msg, messages.RequestTransitConnection):
                self.nursery.start_soon(self.handle_request_transit_connection, msg)
            elif isinstance(msg, messages.CloseTransitConnection):
                await self.handle_close_transit_connection(msg)
            else:
                raise RuntimeError(f"unknown mst-type {msg}")

    async def handle_request_data_chunk(self, msg: messages.DataChunk):
        self.logger.debug(f"handle_request_data_chunk entered {msg}")
        remote_stream = self.opened_streams.get(msg.cookie, None)
        self.logger.debug(f"remote_stream = {remote_stream}")
        if remote_stream:
            await remote_stream.send_all(msg.data)

    async def handle_request_transit_connection(self, msg: messages.RequestTransitConnection):

        try:

            with trio.move_on_after(5):
                remote_stream = await trio.open_tcp_stream(msg.address, msg.port)
                msg_connection_result = messages.ResponseTransitConnection(
                    cookie=msg.cookie, status=socks5.Socks5.SUCCESS)

                self.opened_streams[msg.cookie] = remote_stream

                await messages.write_to_server_stream(self.conn, msg_connection_result)
                self.nursery.start_soon(self.remote_connection_reader, remote_stream, msg.cookie)
                return

            # noinspection PyUnreachableCode
            msg_connection_result_timeout = messages.ResponseTransitConnection(
                cookie=msg.cookie, status=socks5.Socks5.RES_TTL_EXITED)
            await messages.write_to_server_stream(self.conn, msg_connection_result_timeout)
        except Exception as e:
            msg_connection_result_failed = messages.ResponseTransitConnection(
                cookie=msg.cookie, status=socks5.Socks5.RES_SOCKS_ERROR
            )
            await messages.write_to_server_stream(self.conn, msg_connection_result_failed)

    async def remote_connection_reader(self, remote_stream: trio.abc.Stream, cookie: int):
        while True:
            try:
                chunk = await remote_stream.receive_some(2048)
                if chunk:
                    msg_chunk = messages.DataChunk(cookie=cookie, data=chunk)
                    await messages.write_to_server_stream(self.conn, msg_chunk)
            except Exception as e:
                self.logger.debug(f"remote for conn {cookie} was closed ?")
                break

    async def handle_close_transit_connection(self, msg: messages.CloseTransitConnection):
        remote_stream = self.opened_streams.get(msg.cookie, None)
        if remote_stream:
            del self.opened_streams[msg.cookie]
            await remote_stream.aclose()
