import traceback
import logging

import asks
import trio

from quant.common import asks_socks5
import quant.aclient.state as state


logger = logging.getLogger(__name__)

old_asks_get = asks.get


async def asks_get(*args, **kwargs):
    print(f"asks_get[0:arg]: {args[0]}")
    return await old_asks_get(*args, **kwargs)

asks.get = asks_get


class Watcher(object):

    def __init__(self):
        pass

    # noinspection PyMethodMayBeStatic
    async def watcher_loop(self):
        while True:
            await trio.sleep(60)
            try:
                if state.socks_host and state.socks_port and state.socks_user and state.socks_pass:
                    logger.debug("WATCHINS transport....")
                    resp = await asks_socks5.get("https://check-host.net/ip", socks_chains=[dict(
                        host=state.socks_host,
                        port=state.socks_port,
                        user=state.socks_user,
                        password=state.socks_pass
                    )])
                    if resp.content != state.socks_host:
                        logger.warning(f"ip changed ??? {resp.content}")
                    else:
                        logger.info(f"transport ONLINE....")
            except Exception as e:
                logger.debug(f"err = {e} {traceback.format_exc()}")
                pass
