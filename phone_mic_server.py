"""Phone Mic Server — WebSocket server for phone-as-wireless-mic pairing.

Generates a QR code containing a pairing URL. Phone scans it, opens a web page
that captures mic audio via the browser, and streams it over WebSocket to this
server. Audio chunks are pushed into the same queue the local mic uses.
"""

import asyncio
import base64
import io
import logging
import os
import secrets
import socket
import ssl
import struct
import threading
from datetime import datetime, timedelta

import numpy as np

log = logging.getLogger(__name__)

# Lazy imports for optional deps
_qrcode = None
_websockets = None


def _ensure_imports():
    global _qrcode, _websockets
    if _qrcode is None:
        import qrcode
        _qrcode = qrcode
    if _websockets is None:
        import websockets
        _websockets = websockets


def get_local_ip() -> str:
    """Get the machine's local network IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())


def generate_self_signed_cert(cert_path: str, key_path: str):
    """Generate a self-signed cert for HTTPS/WSS on local network."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    local_ip = get_local_ip()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"AXIS Producer ({local_ip})"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address(local_ip)),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


class PhoneMicServer:
    """WebSocket server that receives audio from a phone browser."""

    def __init__(self, chunk_queue, stop_event: threading.Event,
                 ws_port: int = 8081, https_port: int = 8443,
                 verbose: bool = False):
        self.chunk_queue = chunk_queue
        self.stop_event = stop_event
        self.ws_port = ws_port
        self.https_port = https_port
        self.verbose = verbose

        self._token = secrets.token_urlsafe(16)
        self._local_ip = get_local_ip()
        self._paired = False
        self._streaming = False
        self._muted = False
        self._loop = None
        self._thread = None

        # Cert paths
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._cert_path = os.path.join(self._base_dir, "phone_mic_cert.pem")
        self._key_path = os.path.join(self._base_dir, "phone_mic_key.pem")

    @property
    def is_paired(self) -> bool:
        return self._paired

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    @property
    def pairing_url(self) -> str:
        return f"https://{self._local_ip}:{self.https_port}/phone_mic.html?token={self._token}&ws_port={self.ws_port}"

    def regenerate_token(self):
        self._token = secrets.token_urlsafe(16)

    def get_qr_base64(self) -> str:
        """Generate QR code as base64-encoded PNG."""
        _ensure_imports()
        qr = _qrcode.QRCode(box_size=8, border=2)
        qr.add_data(self.pairing_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def get_status(self) -> dict:
        return {
            "paired": self._paired,
            "streaming": self._streaming,
            "muted": self._muted,
            "url": self.pairing_url,
            "qr_base64": f"data:image/png;base64,{self.get_qr_base64()}",
            "local_ip": self._local_ip,
            "ws_port": self.ws_port,
        }

    def _ensure_cert(self):
        if not os.path.exists(self._cert_path) or not os.path.exists(self._key_path):
            if self.verbose:
                print("  [phone-mic] Generating self-signed certificate...")
            generate_self_signed_cert(self._cert_path, self._key_path)

    def _create_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self._cert_path, self._key_path)
        return ctx

    async def _handle_connection(self, websocket):
        """Handle a single phone WebSocket connection."""
        import json as _json
        from urllib.parse import parse_qs, urlparse

        # Validate token
        path = websocket.request.path if hasattr(websocket, 'request') else ""
        params = parse_qs(urlparse(path).query)
        token = params.get("token", [""])[0]

        if token != self._token:
            await websocket.close(4001, "Invalid token")
            return

        if self._paired:
            await websocket.close(4002, "Already paired")
            return

        self._paired = True
        self._streaming = True
        if self.verbose:
            print("  [phone-mic] Phone connected")

        try:
            await websocket.send(_json.dumps({"type": "status", "recording": True}))

            async for message in websocket:
                if self.stop_event.is_set():
                    break

                if isinstance(message, bytes):
                    # Binary frame: raw int16 PCM at 16kHz
                    if not self._muted:
                        chunk = np.frombuffer(message, dtype=np.int16)
                        try:
                            self.chunk_queue.put_nowait(chunk)
                        except Exception:
                            pass  # queue full, drop frame
                elif isinstance(message, str):
                    try:
                        msg = _json.loads(message)
                        msg_type = msg.get("type")
                        if msg_type == "mute":
                            self._muted = True
                        elif msg_type == "unmute":
                            self._muted = False
                        elif msg_type == "ping":
                            await websocket.send(_json.dumps({"type": "pong"}))
                    except _json.JSONDecodeError:
                        pass
        except Exception as e:
            if self.verbose:
                print(f"  [phone-mic] Connection error: {e}")
        finally:
            self._paired = False
            self._streaming = False
            self._muted = False
            if self.verbose:
                print("  [phone-mic] Phone disconnected")

    async def _run_server(self):
        _ensure_imports()
        ssl_ctx = self._create_ssl_context()

        async with _websockets.serve(
            self._handle_connection,
            "0.0.0.0",
            self.ws_port,
            ssl=ssl_ctx,
        ) as server:
            if self.verbose:
                print(f"  [phone-mic] WSS server on port {self.ws_port}")
            while not self.stop_event.is_set():
                await asyncio.sleep(0.5)

    def _thread_target(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_server())
        except Exception as e:
            if self.verbose:
                print(f"  [phone-mic] Server error: {e}")
        finally:
            self._loop.close()

    def start(self):
        self._ensure_cert()
        self._thread = threading.Thread(
            target=self._thread_target, name="phone-mic-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
