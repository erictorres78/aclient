import json
import socket
import traceback
from collections import namedtuple

import attr
import trio

from quant.common import socks5


def JsNamedTuple(*args, **kwargs):
    result = namedtuple(*args, **kwargs)

    def dump(self):
        d = self._asdict()
        d.update(dict(type=self.__class__.__name__))
        return json.dumps(d)

    result.dump = dump

    def immodify(self, **kwargs):
        d = self._asdict()
        d.update(kwargs)
        d.update(dict(type=self.__class__.__name__))
        return load_js_message(json.dumps(d))

    result.immodify = immodify
    js_messages.append(result.__name__)

    return result


js_messages = []


def load_js_message(msg):
    data = json.loads(msg)
    print(f"data = {data}")
    type_ = data['type']
    del data['type']

    print(f"data = {data}")

    assert type_ in js_messages

    result = globals()[type_](**data)
    return result


class AuthStatus:
    SUCEESS = 1
    FAILED = 0


class ActivateStatus:
    SUCCESS = 1
    FAILED = 0


class AuthReason:
    OK = "ok"
    WRONG_TOKEN = "wrong token"


## CS < ==== > TS messages

# TS == > CS
AuthMessage = JsNamedTuple("AuthMessage", "token address port transit_port")
EndpointsList = JsNamedTuple("EndpointsList", "endpoints")
TransitEndpointChanged = JsNamedTuple("TransitEndpointChanged", "token new_address")

# CS ==> TS
AuthResult = JsNamedTuple("AuthResult", "status reason")
# start new transit endpoint for AClients
SetCredentials = JsNamedTuple("SetCredentials", "address user password endpoint")

AddTransitEndpoint = JsNamedTuple("AddTransitEndpoint", "token address")

## CS < === > C messages

# C => CS
MessageConnect = JsNamedTuple("MessageConnect", "id")
MessageActivate = JsNamedTuple("MessageActivate", "endpoint")
MessageAlive = JsNamedTuple("MessageAlive", "id")


# CS ==> C

MessageActivateResult = JsNamedTuple("MessageActivateResult", "status reason credentials")
MessageCredentials = JsNamedTuple("MessageCredentials", "credentials")

MessageActivateTransitEndpoint = JsNamedTuple("MessageActivateTransitEndpoint", "token host port")


print(f"Allowed messages = {js_messages}")


class FromBinary(object):

    @classmethod
    def from_bytes(cls, payload):
        pass


class ToBinary(object):

    def as_bytes(self) -> bytes:
        pass


@attr.s
class TransitEndpointConnection(FromBinary, ToBinary):
    token: str = attr.attrib()

    @classmethod
    def from_bytes(cls, payload):
        tlen = payload[0]
        token = payload[1:1 + tlen]
        return cls(token=token.decode())

    def as_bytes(self) -> bytes:
        return \
            bytes([len(self.token)]) + self.token.encode()


@attr.s
class RequestTransitConnection(ToBinary, FromBinary):
    cookie: int = attr.attrib()
    addr_type: int = attr.attrib()
    address: str = attr.attrib()
    port: int = attr.attrib()
    queue: trio.abc.SendChannel = attr.attrib()

    def as_bytes(self):

        if self.addr_type == socks5.Socks5.ADDR_DOMAIN:
            addr_payload = (len(self.address)).to_bytes(length=1, byteorder='big') + \
                self.address.encode()
        elif self.addr_type == socks5.Socks5.ADDR_IPv4:
            addr_payload = socket.inet_aton(self.address)
        else:
            raise NotImplemented(f"unsupported addr type {self.addr_type}")
        return \
            self.cookie.to_bytes(length=4, byteorder='big') + \
            self.addr_type.to_bytes(length=1, byteorder='big') + \
            addr_payload + \
            self.port.to_bytes(length=2, byteorder='big')

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        addr_type = int.from_bytes(bytes([payload[4]]), byteorder='big')
        if addr_type == socks5.Socks5.ADDR_IPv4:
            address = socket.inet_ntoa(payload[5:9])
            port = int.from_bytes(payload[9: 9 + 2], byteorder='big')
        elif addr_type == socks5.Socks5.ADDR_DOMAIN:
            addr_len = int.from_bytes(bytes([payload[5]]), byteorder='big')
            address = payload[6:6 + addr_len]
            port_bytes = payload[6 + addr_len: 6 + addr_len + 2]
            port = int.from_bytes(port_bytes, byteorder='big')
            print(f'addr_len = {addr_len} address = {address} port_bytes = {port_bytes} port = {port}')
        else:
            raise NotImplemented(f"unsupported addr type {addr_type}")
        result = cls(
            cookie=cookie,
            address=address,
            port=port,
            addr_type=addr_type,
            queue=None
        )
        print(f"MSG: result = {result}")
        return result


@attr.s
class ResponseTransitConnection(FromBinary, ToBinary):
    cookie: int = attr.attrib()
    status: int = attr.attrib()

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        status = int.from_bytes(payload[4:8], byteorder='big')
        return cls(cookie=cookie, status=status)

    def as_bytes(self) -> bytes:
        return \
            self.cookie.to_bytes(length=4, byteorder='big') + \
            self.status.to_bytes(length=4, byteorder='big')


@attr.s
class CloseTransitConnection(ToBinary, FromBinary):
    cookie: int = attr.attrib()

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        return cls(cookie=cookie)

    def as_bytes(self):
        return self.cookie.to_bytes(length=4, byteorder='big')


@attr.s
class DataChunk(ToBinary, FromBinary):
    cookie: int = attr.attrib()
    data: bytes = attr.attrib()

    def as_bytes(self):
        data = self.cookie.to_bytes(length=4, byteorder='big') + \
               (len(self.data)).to_bytes(length=2, byteorder='big') + \
            self.data
        return data

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        data_len = int.from_bytes(payload[4:6], byteorder='big')
        data = payload[6:6 + data_len]
        return cls(cookie=cookie, data=data)


@attr.s
class ConnectionEsteblished(FromBinary):
    cookie: int = attr.attrib()

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        return cls(cookie=cookie)


@attr.s
class ConnectionEsteblishError(FromBinary):
    cookie: int = attr.attrib()
    reason: int = attr.attrib()

    @classmethod
    def from_bytes(cls, payload):
        cookie = int.from_bytes(payload[:4], byteorder='big')
        reason = payload[4]
        return cls(cookie=cookie, reason=reason)


class MsgTypes:
    OPEN = 0
    CLOSE = 1
    DATA = 2
    ESTEBLISHED = 3
    NETERROR = 4

    TRANSIT_CONNECTION = 0xf1
    TRANSIT_CONNECTION_RESPONSE = 0xf2


out_map = {
    RequestTransitConnection: MsgTypes.OPEN,
    CloseTransitConnection: MsgTypes.CLOSE,
    DataChunk: MsgTypes.DATA,
    TransitEndpointConnection: MsgTypes.TRANSIT_CONNECTION,
    ResponseTransitConnection: MsgTypes.TRANSIT_CONNECTION_RESPONSE
}

in_map = {
    MsgTypes.ESTEBLISHED: ConnectionEsteblished,
    MsgTypes.NETERROR: ConnectionEsteblishError,
    MsgTypes.CLOSE: CloseTransitConnection,
    MsgTypes.DATA: DataChunk,
    MsgTypes.TRANSIT_CONNECTION: TransitEndpointConnection,

    MsgTypes.OPEN: RequestTransitConnection,
    MsgTypes.TRANSIT_CONNECTION_RESPONSE: ResponseTransitConnection
}


async def read_from_server_stream(stream: trio.abc.Stream) -> FromBinary:
    try:
        print(f"entered to read_from_server_stream")
        request_size = await stream.receive_some(2)
        request_size = int.from_bytes(request_size, signed=False, byteorder='big')
        print(f"rs = {request_size}")
        request_data_chunks = []
        request_received_size = 0
        while request_received_size < request_size:
            data_chunk = await stream.receive_some(request_size - request_received_size)
            if not data_chunk:
                return None
            request_received_size += len(data_chunk)
            request_data_chunks.append(data_chunk)
        print(f"rdc = {request_data_chunks}")
        raw_request = b"".join(request_data_chunks)
        request_type = raw_request[0]
        request_payload = raw_request[1:]
        request = in_map[request_type].from_bytes(request_payload)
        return request
    except Exception as e:
        print(f"read_from_server_stream EX: {e} {traceback.format_exc()}")
        return None


async def write_to_server_stream(stream: trio.abc.Stream, obj: ToBinary):
    try:
        print(f"entered to write_to_server_stream {obj}")
        bin_obj: bytes = obj.as_bytes()
        bin_obj_type = out_map[type(obj)]
        bin_obj_type = bytes([bin_obj_type])
        bin_len = (1 + len(bin_obj)).to_bytes(length=2, byteorder='big')
        await stream.send_all(b"".join([bin_len, bin_obj_type, bin_obj]))
    except Exception as e:
        print(f"write_to_server_stream EX: {e} {traceback.format_exc()} {obj}")
        return None


if __name__ == "__main__":
    auth_message = AuthMessage(token="testtoken")

    print(auth_message)
    print(dict(auth_message._asdict()))

    msg_dict = auth_message._asdict()
    msg_dict.update({'type': auth_message.__class__.__name__})
    print(json.dumps(dict(msg_dict)))

    auth_msg_str = '{"token": "testtoken", "type": "AuthMessage"}'
    auth_msg_loaded = load_js_message(auth_msg_str)
    print(auth_msg_loaded)
    assert isinstance(auth_msg_loaded, AuthMessage) is True

    raw_msg_str2 = auth_msg_loaded.dump()

    msg_loaded_2 = load_js_message(raw_msg_str2)
    print(msg_loaded_2)
    assert isinstance(msg_loaded_2, AuthMessage) is True

    msg_loaded_3 = msg_loaded_2.immodify(token="testtoken3")
    print(msg_loaded_3)
    assert msg_loaded_3.token == "testtoken3"
