import heapq
import time
import socket
import threading
import os
from collections import deque, defaultdict
import discord
from logging import getLogger

logger = getLogger(__name__)

FRAME_SIZE = 3840 # 20ms of 48kHz 16-bit stereo PCM
PCM_SILENCE_FRAME = b'\x00' * FRAME_SIZE

DEFAULT_RELAY_PORT = int(os.getenv("AUDIO_RELAY_PORT", 9999))
DEFAULT_RELAY_HOST = os.getenv("AUDIO_RELAY_HOST", "0.0.0.0")

class JitterBuffer:
    """
    Jitter Buffer & Packet Ordering Engine.
    Sorts incoming audio packets by sequence number and absorbs network jitter.
    """
    def __init__(self, target_depth: int = 5, max_capacity: int = 50):
        self.target_depth = target_depth # 5 frames = 100ms
        self.max_capacity = max_capacity
        self.heap = [] # Min-heap of (seq, timestamp, payload)
        self.lock = threading.Lock()
        self.last_seq = -1
        self.prebuffered = False

    def push(self, seq: int, payload: bytes):
        with self.lock:
            # Handle sequence number wrap-around (16-bit)
            if self.last_seq != -1:
                diff = (seq - self.last_seq) % 65536
                if diff > 30000: # Late / duplicate old packet
                    return
            
            heapq.heappush(self.heap, (seq, time.monotonic(), payload))
            
            if len(self.heap) > self.max_capacity:
                heapq.heappop(self.heap)

            if len(self.heap) >= self.target_depth:
                self.prebuffered = True

    def pop(self) -> bytes:
        with self.lock:
            if not self.prebuffered:
                if len(self.heap) >= self.target_depth:
                    self.prebuffered = True
                else:
                    return PCM_SILENCE_FRAME

            if self.heap:
                seq, _, payload = heapq.heappop(self.heap)
                self.last_seq = seq
                return payload

            self.prebuffered = False
            return PCM_SILENCE_FRAME

    def clear(self):
        with self.lock:
            self.heap.clear()
            self.last_seq = -1
            self.prebuffered = False


class AudioClock:
    """
    High-precision 20ms (50Hz) Audio Timing Engine.
    Ensures precise 20.0ms intervals for Discord voice streaming.
    """
    def __init__(self, callback, interval_ms: float = 20.0):
        self.callback = callback
        self.interval = interval_ms / 1000.0
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._clock_loop, daemon=True)
        self.thread.start()

    def _clock_loop(self):
        next_tick = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            delay = next_tick - now
            if delay > 0:
                time.sleep(delay)
            
            next_tick += self.interval
            try:
                self.callback()
            except Exception as e:
                logger.debug(f"[AudioClock] Callback error: {e}")

    def stop(self):
        self.running = False


class StandaloneAudioRelay:
    """
    Standalone Audio Relay Core combining:
    1. Receiver UDP Input (from Source Bot)
    2. Jitter Buffer & Packet Ordering per stream
    3. High-precision 20ms Audio Clock
    4. Fan-Out Broadcaster to Target Bots (1..8)
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, host=DEFAULT_RELAY_HOST, port=DEFAULT_RELAY_PORT):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(host=host, port=port)
            return cls._instance

    def __init__(self, host=DEFAULT_RELAY_HOST, port=DEFAULT_RELAY_PORT):
        self.host = host
        self.port = port
        self.running = False
        self.sock = None
        self.jitter_buffers = defaultdict(JitterBuffer)
        self.stream_seq = defaultdict(int)
        self._server_thread = None
        self.subscribers = defaultdict(set) # stream_id -> set of UDP subscriber addrs

    def start(self):
        if self.running:
            return
        self.running = True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self.sock.settimeout(0.5)
            logger.info(f"[AUDIO RELAY ENGINE] UDP Socket listening on {self.host}:{self.port}")
            print(f"[AUDIO RELAY ENGINE] Listening on UDP {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"[AUDIO RELAY ENGINE] Socket bind error: {e}")
            self.sock = None

        self._server_thread = threading.Thread(target=self._udp_receiver_loop, daemon=True)
        self._server_thread.start()

    def _udp_receiver_loop(self):
        """
        Receives UDP packets from Source Bot Receiver.
        Format: 32-byte header (stream_id) + 3840 bytes PCM data
        """
        while self.running and self.sock:
            try:
                data, addr = self.sock.recvfrom(4096)
                if data and len(data) >= 32 + FRAME_SIZE:
                    stream_id = data[:32].decode('utf-8', errors='ignore').rstrip('\x00')
                    pcm_payload = data[32:32 + FRAME_SIZE]

                    if stream_id and len(pcm_payload) == FRAME_SIZE:
                        # Auto-increment sequence number for jitter buffer
                        self.stream_seq[stream_id] = (self.stream_seq[stream_id] + 1) % 65536
                        seq = self.stream_seq[stream_id]

                        # Push into stream's JitterBuffer
                        self.jitter_buffers[stream_id].push(seq, pcm_payload)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.debug(f"[AUDIO RELAY ENGINE] Recv error: {e}")

    def pop_frame(self, stream_id: str) -> bytes:
        """
        Fetches the next ordered, jitter-buffered 20ms PCM frame for the stream.
        """
        return self.jitter_buffers[stream_id].pop()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
