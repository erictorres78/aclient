
'''
These functions are for making small amounts of async requests.
They construct a temporary Session, returning the resulting response object
to the caller.
'''
import logging
import socket
import traceback
from functools import partial
from ssl import SSLContext
from typing import Optional

import trio
from anyio import _get_asynclib, run_in_thread, _networking, SocketStream, IPAddressType
from asks.response_objects import Response
from asks.sessions import Session


from quant.common import socks5



__all__ = ['get', 'head', 'post', 'put', 'delete', 'options', 'request']


async def connect_tcp_socksified(
        address: IPAddressType, port: int, chains, *, ssl_context: Optional[SSLContext] = None,
        autostart_tls: bool = False, bind_host: Optional[IPAddressType] = None,
        bind_port: Optional[int] = None, tls_standard_compatible: bool = True
) -> SocketStream:
    """
    Connect to a host using the TCP protocol.

    :param address: the IP address or host name to connect to
    :param port: port on the target host to connect to
    :param ssl_context: default SSL context to use for TLS handshakes
    :param autostart_tls: ``True`` to do a TLS handshake on connect
    :param bind_host: the interface address or name to bind the socket to before connecting
    :param bind_port: the port to bind the socket to before connecting
    :param tls_standard_compatible: If ``True``, performs the TLS shutdown handshake before closing
        the stream and requires that the server does this as well. Otherwise,
        :exc:`~ssl.SSLEOFError` may be raised during reads from the stream.
        Some protocols, such as HTTP, require this option to be ``False``.
        See :meth:`~ssl.SSLContext.wrap_socket` for details.
    :return: a socket stream object

    """
    interface, family = None, 0  # type: Optional[str], int
    if bind_host:
        interface, family, _v6only = await _networking.get_bind_address(bind_host)

    socks_host, socks_port = chains[0]['host'], chains[0]['port']
    socks_user, socks_pass = chains[0].get('user', None), chains[0].get('password', None)

    if socks_user and socks_pass:
        method = socks5.Socks5.USERPASS
    else:
        method = socks5.Socks5.NOAUTH

    transfer_stream = await trio.open_tcp_stream(socks_host, socks_port)
    if await socks5.Socks5Client.process_hello(socks5.Socks5Client, transfer_stream, method=method):
        if method == socks5.Socks5.USERPASS:
            if await socks5.Socks5Client.process_auth(socks5.Socks5Client, transfer_stream, socks_user, socks_pass):
                if await socks5.Socks5Client.process_establish_transfer(socks5.Socks5Client, transfer_stream, str(address), port):
                    print(f"CONNECTION ESTABLISHED !!!")
                    sock = transfer_stream.socket._sock

                    # getaddrinfo() will raise an exception if name resolution fails
                    # address = str(address)
                    # addrlist = await run_in_thread(socket.getaddrinfo, address, port, family, socket.SOCK_STREAM)
                    # family, type_, proto, _cn, sa = addrlist[0]
                    # raw_socket = socket.socket(family, type_, proto)
                    # sock = _get_asynclib().Socket(raw_socket)
                    sock = _get_asynclib().Socket(sock)
                    try:
                        # sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        # if interface is not None and bind_port is not None:
                        #     await sock.bind((interface, bind_port))
                        #
                        # await sock.connect(sa)
                        stream = _networking.SocketStream(sock, ssl_context, address, tls_standard_compatible)
                        print(f"stream = {stream}, ast_tls = {autostart_tls}")
                        print(dir(stream))
                        print(stream.start_tls)

                        if autostart_tls:
                            await stream.start_tls()

                        return stream
                    except BaseException as e:
                        print(f"EXCEPTION! {e} {traceback.format_exc()}")
                        await sock.close()
                        raise
        else:
            if await socks5.Socks5Client.process_establish_transfer(socks5.Socks5Client, transfer_stream, str(address),
                                                                    port):
                print(f"CONNECTION ESTABLISHED !!!")
                sock = transfer_stream.socket._sock

                # getaddrinfo() will raise an exception if name resolution fails
                # address = str(address)
                # addrlist = await run_in_thread(socket.getaddrinfo, address, port, family, socket.SOCK_STREAM)
                # family, type_, proto, _cn, sa = addrlist[0]
                # raw_socket = socket.socket(family, type_, proto)
                # sock = _get_asynclib().Socket(raw_socket)
                sock = _get_asynclib().Socket(sock)
                try:
                    # sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    # if interface is not None and bind_port is not None:
                    #     await sock.bind((interface, bind_port))
                    #
                    # await sock.connect(sa)
                    stream = _networking.SocketStream(sock, ssl_context, address, tls_standard_compatible)
                    print(f"stream = {stream}, ast_tls = {autostart_tls}")
                    print(dir(stream))
                    print(stream.start_tls)

                    if autostart_tls:
                        await stream.start_tls()

                    return stream
                except BaseException as e:
                    print(f"EXCEPTION! {e} {traceback.format_exc()}")
                    await sock.close()
                    raise


class SocksifiedSession(Session):

    def __init__(self, base_location=None, endpoint=None, headers=None, encoding='utf-8', persist_cookies=None,
                 ssl_context=None, connections=1, chains=None):
        super().__init__(base_location, endpoint, headers, encoding, persist_cookies, ssl_context, connections)
        self.chains = chains if chains else []

    async def _open_connection_http(self, location):
        if len(self.chains) == 0:
            return await super()._open_connection_http(location)
        else:
            sock = await connect_tcp_socksified(location[0], location[1], bind_host=self.source_address, chains=self.chains)
            sock._active = True
            return sock

    async def _open_connection_https(self, location):
        if len(self.chains) == 0:
            return await super()._open_connection_https(location)
        else:
            sock = await connect_tcp_socksified(location[0],
                                     location[1],
                                     ssl_context=self.ssl_context,
                                     bind_host=self.source_address,
                                     autostart_tls=True,
                                     tls_standard_compatible=False, chains=self.chains)
            if not sock:
                return sock
            sock._active = True
            return sock


async def request(method, uri, socks_chains = None, **kwargs):
    '''Base function for one time http requests.

    Args:
        method (str): The http method to use. For example 'GET'
        uri (str): The url of the resource.
            Example: 'https://example.com/stuff'
        kwargs: Any number of arguments supported, found here:
            http://asks.rtfd.io/en/latest/overview-of-funcs-and-args.html

    Returns:
        Response (asks.Response): The Response object.
    '''
    c_interact = kwargs.pop('persist_cookies', None)
    ssl_context = kwargs.pop('ssl_context', None)
    async with SocksifiedSession(persist_cookies=c_interact, ssl_context=ssl_context, chains=socks_chains) as s:
        r = await s.request(method, url=uri, **kwargs)
        return r

# The functions below are the exact same as the ``request`` function
# above, with the method argument already passed.
get = partial(request, 'GET')
head = partial(request, 'HEAD')
post = partial(request, 'POST')
put = partial(request, 'PUT')
delete = partial(request, 'DELETE')
options = partial(request, 'OPTIONS')


if __name__ == "__main__":

    async def test_socks():

        resp: Response = await request('GET', "https://check-host.net/ip", socks_chains=[dict(host="127.0.0.1", port=1083, user='test', password='test')])

        print(resp)
        print(resp.content)

    logging.basicConfig(level=logging.DEBUG)

    trio.run(test_socks)
