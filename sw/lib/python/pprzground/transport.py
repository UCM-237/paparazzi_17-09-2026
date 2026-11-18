"""Byte-stream transports of the OCaml pprzlink library: PPRZ (STX 0x99)
and XBee API (0x7E), with the same frame parsing and error accounting."""


class NotEnough(Exception):
    pass


class _Transport:
    def __init__(self):
        self.nb_err = 0
        self.discarded_bytes = 0

    def parse(self, use, buf):
        """Protocol.Transport.parse: returns the number of bytes consumed."""
        consumed = 0
        while True:
            n = len(buf)
            try:
                start = self.index_start(buf)
            except ValueError:
                return consumed + n
            self.discarded_bytes += start
            try:
                length = self.length(buf, start)
            except NotEnough:
                return consumed + start
            end = start + length
            if n < end:
                return consumed + start
            msg = buf[start:end]
            if self.checksum(msg):
                use(self.payload(msg))
            else:
                self.nb_err += 1
                self.discarded_bytes += length
            consumed += end
            buf = buf[end:]


class PprzTransport(_Transport):
    STX = 0x99
    OVERHEAD = 4

    def index_start(self, buf):
        return buf.index(self.STX)

    def length(self, buf, start):
        if len(buf) - start > 1:
            return max(buf[start + 1], self.OVERHEAD)
        raise NotEnough()

    @staticmethod
    def compute_checksum(msg):
        ck_a = ck_b = 0
        for i in range(1, len(msg) - 2):
            ck_a = (ck_a + msg[i]) & 0xFF
            ck_b = (ck_b + ck_a) & 0xFF
        return ck_a, ck_b

    def checksum(self, msg):
        a, b = self.compute_checksum(msg)
        return a == msg[-2] and b == msg[-1]

    def payload(self, msg):
        return msg[2:len(msg) - 2]

    @classmethod
    def packet(cls, payload):
        n = len(payload) + cls.OVERHEAD
        if n >= 256:
            raise ValueError("Pprz_transport.Transport.packet")
        m = bytearray([cls.STX, n]) + payload + b"\x00\x00"
        a, b = cls.compute_checksum(m)
        m[-2], m[-1] = a, b
        return bytes(m)


class XbeeTransport(_Transport):
    START = 0x7E

    def index_start(self, buf):
        return buf.index(self.START)

    def length(self, buf, i):
        if len(buf) < i + 3:
            raise NotEnough()
        return ((buf[i + 1] << 8) | buf[i + 2]) + 4

    @staticmethod
    def compute_checksum(s):
        cs = 0
        for i in range(3, len(s) - 1):
            cs = (cs + s[i]) & 0xFF
        return 0xFF - cs

    def checksum(self, s):
        return self.compute_checksum(s) == s[-1]

    def payload(self, s):
        return s[3:len(s) - 1]

    @classmethod
    def packet(cls, payload):
        n = len(payload)
        m = bytearray([cls.START, (n >> 8) & 0xFF, n & 0xFF]) + payload + b"\x00"
        m[-1] = cls.compute_checksum(m)
        return bytes(m)


# ---------------------------------------------------------------- XBee API

API_TX64, API868_TX64, API_TX16 = 0x00, 0x10, 0x01
API_RX64, API_RX16, API868_RX64 = 0x80, 0x81, 0x90
API_AT_RESPONSE, API_TX_STATUS, API868_TX_STATUS, API_MODEM_STATUS = 0x88, 0x89, 0x8B, 0x8A


def api_tx64(dest, data, frame_id=0, mode868=False):
    assert 0 <= frame_id < 0x100 and len(data) <= 100
    opt = 3 if mode868 else 0
    s = bytearray(1 + 1 + 8 + opt + 1 + len(data))
    s[0] = API_TX64
    s[1] = frame_id
    if mode868:
        s[10], s[11], s[12] = 0xFF, 0xFE, 0
    for i in range(8):
        s[2 + 7 - i] = (dest >> (8 * i)) & 0xFF
    s[10 + opt] = 0
    s[11 + opt:] = data
    return bytes(s)


def api_tx16(dest, data, frame_id=0, mode868=False):
    if mode868:
        raise RuntimeError("Xbee.api_tx16 not available in mode868")
    assert 0 <= frame_id < 0x100 and len(data) <= 100 and 0 <= dest < 0x10000
    return bytes([API_TX16, frame_id, dest >> 8, dest & 0xFF, 0]) + data


def at_set_my(addr):
    assert 0 <= addr < 0x10000
    return "ATMY%04x\r" % addr


def at_set_channel(ch):
    assert 0x0C <= ch <= 0x17
    return "ATCH%X\r" % ch


AT_COMMAND_SEQUENCE = "+++"
AT_EXIT = "ATCN\r"
AT_API_ENABLE = "ATAP1\r"


def api_parse_frame(s, mode868=False):
    """returns (kind, ...) like Xbee_transport.api_parse_frame"""
    n = len(s)
    assert n > 0
    x = s[0]
    if x == API_AT_RESPONSE:
        assert n >= 5
        return ("at_response", s[1], s[2:4], s[4], s[5:])
    if not mode868 and x == API_TX_STATUS:
        assert n == 3
        return ("tx_status", s[1], s[2])
    if mode868 and x == API868_TX_STATUS:
        assert n == 7
        return ("tx_status", s[1], s[5])
    if x == API_MODEM_STATUS:
        return ("modem_status", s[1])
    if not mode868 and x == API_RX64:
        assert n >= 11
        return ("rx", s[11:])
    if mode868 and x == API868_RX64:
        assert n >= 12
        return ("rx", s[12:])
    if not mode868 and x in (API_RX16, API_TX16):
        return ("rx", s[5:])
    raise RuntimeError("Xbee.parse_frame: unknown frame id '%d'" % x)
