import logging
import socket

import trio


class Socks5:
    SUCCESS = 0x0
    V5 = 5
    V4 = 4
    NOAUTH = 0
    USERPASS = 0x02

    RESERVED = 0x00

    COMMAND_OPEN_TCP = 0x01
    COMMAND_BIND_TCP = 0x02
    COMMAND_ASSOC_UDP = 0x03

    ADDR_IPv4 = 0x01
    ADDR_DOMAIN = 0x03
    ADDR_IPv6 = 0x04

    NOAUTHFOUND = 0xff

    RES_SUCCESS = 0x00
    RES_SOCKS_ERROR = 0x01
    RES_DENIED = 0x02
    RES_NETWORK_UNREACHABLE = 0x03
    RES_HOST_UNREACHEABLE = 0x04
    RES_REJECTED = 0x05
    RES_TTL_EXITED = 0x06
    RES_COMMAND_UNSUPPORTED = 0x07
    RES_ADDRESS_UNSUPPORTED = 0x08
    
    
class SocksConnectError(Exception):
    pass


class FirstChunkConnectError(SocksConnectError):
    pass


class Socks5Server(object):

    def __init__(self, port):
        self.port = port
        self.logger = logging.getLogger(f"[{port}]")

    async def connection_handler(self, stream):
        try:
            await self.request_handler(stream)
        except Exception as e:
            self.logger.error(f"err {e}")

    async def receive_hello(self, stream):
        request = await stream.receive_some(2)
        if request[0] != Socks5.V5:
            return False
        count = request[1]
        self.logger.debug(f"methods count = {count}")
        methods = await stream.receive_some(count)
        for method in methods:
            if method == Socks5.NOAUTH:
                return method
        return False

    async def send_no_auth_methods_found(self, stream: trio.abc.Stream):
        await stream.send_all(bytes([Socks5.V5, Socks5.NOAUTHFOUND]))

    async def receive_command(self, stream: trio.abc.Stream):
        ver, comm, res, addr_type = await stream.receive_some(4)

        self.logger.debug(f"{ver}, {comm}, {res}, {addr_type}")

        if ver != Socks5.V5:
            raise Exception("proto version error")
        if comm not in (Socks5.COMMAND_OPEN_TCP, ):
            raise Exception("unsupported command")

        if addr_type == Socks5.ADDR_IPv4:
            ip = await stream.receive_some(4)
            port = await stream.receive_some(2)
            self.logger.debug(f"ip = {ip}, port = {port}")
            return addr_type, socket.inet_ntoa(ip), int.from_bytes(port, byteorder='big')
        elif addr_type == Socks5.ADDR_DOMAIN:
            l = await stream.receive_some(1)
            # self.logger.debug(f"l = {l}, {type(l)}")
            domain = await stream.receive_some(l[0])
            port = await stream.receive_some(2)
            self.logger.debug(f"addr = {domain}, port = {port}")
            return addr_type, domain, int.from_bytes(port, byteorder='big')
        else:
            self.logger.debug(f"unsupported addr ...")
            raise Exception("unsupported addr")

    async def esteblish(self, stream: trio.abc.Stream):

        # receive hello
        method = await self.receive_hello(stream)
        self.logger.debug(f"method = {method}")
        if method is not False:
            self.logger.debug(f"sending success hello")
            await stream.send_all(bytes([Socks5.V5, method]))
            return True
        else:
            await self.send_no_auth_methods_found(stream)
            await stream.aclose()
            return False

    async def request_handler(self, stream: trio.abc.Stream):
        self.logger.debug(f"request_handler called {stream}")

        if await self.esteblish(stream) is False:
            return

        # receive command
        try:
            addr_type, address, port = await self.receive_command(stream)
            self.logger.debug(f"addr_type = {addr_type} addr = {address} port = {port}")
        except Exception as e:
            import traceback
            self.logger.debug(f"EXCEPTION {e} = {traceback.format_exc()}")
            await stream.send_all(bytes([Socks5.V5, 0x07, 0x00, 0x01, 0x00]))
            await stream.aclose()
            return

        connection = await self.get_connection(addr_type, address, port, stream=stream)
        if connection:
            await self.send_success_connection(stream, addr_type, address, port)
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.tunnel, stream, connection)
                nursery.start_soon(self.tunnel, connection, stream)
            self.logger.debug(f"transfer is done")
        else:
            await stream.send_all(bytes([Socks5.V5, 0x07, 0x00, 0x01, 0x00]))
            await stream.aclose()
            return

    async def get_connection(self, addr_type, address, port, stream=None):
        return await trio.open_tcp_stream(address, port)

    async def tunnel(self, source: trio.abc.Stream, destination: trio.abc.Stream):
        try:
            while True:
                data = await source.receive_some(1024)
                count = len(data)
                if count > 0:
                    await destination.send_all(data)
                    self.logger.debug(f"count {count} from {source} --> {destination}")
                else:
                    self.logger.debug(f"destination from {source} --> {destination} is done")
                    break
        except Exception as e:
            pass

    async def send_success_connection(self, stream: trio.abc.Stream, addr_type, address, port):
        await stream.send_all(bytes([Socks5.V5, 0x00, 0x00, addr_type] + [0x00] * 6))

    async def run(self):
        self.logger.debug("start serving ...")
        await trio.serve_tcp(self.connection_handler, self.port)


class Socks5Client(object):

    logger = logging.getLogger(f"[UNDEFINED]")

    def __init__(self, chains, address, port):
        self.address = address
        self.port = port
        self.chains = chains
        chains_info = "-->".join([f"{x[0]}:{x[1]}" for x in chains])
        self.logger = logging.getLogger(f"[SC {chains_info}==>{address}:{port}]")

    async def get_connection(self):
        self.logger.debug("getting connection...")
        if len(self.chains) == 0:
            stream = await trio.open_tcp_stream(self.address, self.port)
            return stream

        first_chain = self.chains.pop(0)
        try:
            stream: trio.abc.Stream = await trio.open_tcp_stream(*first_chain)
        except Exception as e:
            raise FirstChunkConnectError
        self.logger.debug(f"first chain stream {stream}")
        
        try:

            chains_established = True
            if not stream:
                return None

            for host, port in self.chains:

                if await self.process_hello(stream):

                    if await self.process_establish_transfer(stream, host, port):
                        self.logger.debug(f"established transfer ...")
                    else:
                        chains_established = False
                        continue

                else:
                    chains_established = False
                    continue
            self.logger.debug(f"chains_established = {chains_established}")
            if chains_established:
                if await self.process_hello(stream):
                    self.logger.debug(f"last hello processed")
                    if await self.process_establish_transfer(stream, self.address, self.port):
                        self.logger.debug(f"last transfer end-point established")
                        self.logger.debug("connected")
                        return stream
                    else:
                        await stream.aclose()
                        return None
                else:
                    await stream.aclose()
                    return None

            else:
                if stream:
                    await stream.aclose()
                    return None
        except Exception as e:
            raise SocksConnectError

    async def __aenter__(self):
        stream = await self.get_connection()
        return stream

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def process_hello(self, stream: trio.abc.Stream, method=Socks5.NOAUTH):
        await stream.send_all(bytes([Socks5.V5, 0x1, method]))
        response = await stream.receive_some(2)
        if response[0] == Socks5.V5:
            if response[1] == method:
                return True
        return False

    async def process_auth(self, stream: trio.abc.Stream, user: str, password: str):

        await stream.send_all(
            bytes([0x01]) +
            bytes([len(user)]) + user.encode() +
            bytes([len(password)]) + password.encode()
        )
        response = await stream.receive_some(2)
        return (response[0] == 0x01) and (response[1] == 0x00)

    async def process_establish_transfer(self, stream: trio.abc.Stream, host, port):
        self.logger.debug(f"process_esteblish_transfer entered {host}:{port}")
        try:
            ip = socket.inet_aton(host)
            addr_type = Socks5.ADDR_IPv4
            request = bytes([Socks5.V5, Socks5.COMMAND_OPEN_TCP, Socks5.RESERVED, addr_type])
            request = request + ip + port.to_bytes(byteorder='big', length=2)
            await stream.send_all(request)
        except Exception:
            addr_type = Socks5.ADDR_DOMAIN
            request = bytes([Socks5.V5, Socks5.COMMAND_OPEN_TCP, Socks5.RESERVED, addr_type])
            request = request + (int(len(host))).to_bytes(byteorder='big', length=1)
            request = request + host.encode() + port.to_bytes(byteorder='big', length=2)
            print(f"request = {request}")
            await stream.send_all(request)

        response = await stream.receive_some(4)
        print(f"rest response = {response}")
        if response[0] != Socks5.V5:
            return False
        if response[1] != Socks5.SUCCESS:
            return False
        if response[2] != Socks5.RESERVED:
            return False
        if (response[3] != Socks5.ADDR_IPv4) and (response[3] != Socks5.ADDR_DOMAIN):
            return False
        self.logger.debug(f"receiving last socks5-connect chunk")
        rest = await stream.receive_some(100)
        self.logger.debug(f"last chunk {rest}")
        return True
