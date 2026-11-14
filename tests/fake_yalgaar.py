"""A fake Yalgaar broker that accepts many connections (one per logger channel)."""
import socket
import threading
import time

from tests.test_yalgaar_client import _read_packet, _varint


class MultiBroker(threading.Thread):
    def __init__(self, plan=None, drop_first=False):
        super().__init__(daemon=True)
        self.plan = plan or {}                  # serial -> list of payload strings sent after the SUBACK
        self.drop_first = drop_first            # close the first connection of each channel after sending
        self.clients, self.topics, self.sessions = [], [], {}
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(50)
        self.port = self.srv.getsockname()[1]
        self._dropped = set()
        self.start()

    def run(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(30)
        try:
            while True:
                t, body = _read_packet(conn)
                if t is None:
                    break
                if t == 0x10:
                    cl = int.from_bytes(body[10:12], "big")
                    self.clients.append(body[12:12 + cl].decode())
                    conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                elif t == 0x82:
                    tl = int.from_bytes(body[2:4], "big")
                    topic = body[4:4 + tl].decode()
                    self.topics.append(topic)
                    serial = topic.rsplit("/", 1)[1]
                    conn.sendall(bytes([0x90, 0x03]) + body[:2] + bytes([1]))
                    for payload in self.plan.get(serial, []):
                        data = len(topic).to_bytes(2, "big") + topic.encode() + payload.encode()
                        conn.sendall(bytes([0x30]) + _varint(len(data)) + data)
                    if self.drop_first and serial not in self._dropped:
                        self._dropped.add(serial)
                        time.sleep(0.5)
                        break
                elif t == 0xC0:
                    conn.sendall(bytes([0xD0, 0x00]))
                elif t == 0xE0:
                    break
        except Exception:
            pass
        finally:
            conn.close()