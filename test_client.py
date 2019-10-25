import logging
import os
import traceback

import trio
import asks
from asks.response_objects import Response
import trio_websocket
import trio_asyncio
import async_property

import quant.common.asks_socks5 as asks_socks5
from quant.aclient.handler_control import ControlConnectionHandler
from quant.aclient.watcher import Watcher
from quant.common.socks5 import Socks5Client, SocksConnectError, FirstChunkConnectError
from quant.common import messages


logger = logging.getLogger(__name__)


async def control_connection(url, id):
    while True:
        try:
            control_connection_handler = ControlConnectionHandler(id, url)
            await control_connection_handler.handle()
        except Exception as e:
            logger.warning(f"cannot create control connection handler {e} {traceback.format_exc()}")
        finally:
            await trio.sleep(3)


async def main():
    url = os.environ.get("URL", "http://localhost:8087")
    ID = os.environ.get("ID", "ID00000001")

    watcher = Watcher()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(control_connection, url, ID)
        nursery.start_soon(watcher.watcher_loop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    trio.run(main)
