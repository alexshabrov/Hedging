import time, traceback
from threading import Thread
from queue import Queue
from websocket import WebSocketApp

from .logger import get_logger

class WsClient:
    def __init__(self, url: str, threaded: bool = False, retries: bool = True):
        # Params
        self.url = url
        self.threaded = threaded
        self.retries = retries

        # Properties
        self.ws_app = None
        self.on_message = None
        self.should_run = True
        self.thread = None

        # Request and connection queues
        self.req_queue = Queue()
        self.connect_queue = Queue()

        # Logger
        self.logger = get_logger('ws_client')

    def set_on_message(self, callback):
        self.on_message = callback

    def wait_for_connect(self, timeout: int = 60):
        if self._is_connected():
            return
        
        try:
            self.connect_queue.get(timeout=timeout)
        except Exception as e:
            raise TimeoutError(f"Connection timeout after {timeout} seconds: {e}")

    def _on_message(self, ws, message):
        if self.threaded:
            Thread(target=self.on_message, args=(message,), daemon=True).start()
        else:
            try:
                self.on_message(message)
            except Exception as e:
                self.logger.error(f"Error in on_message callback: {e} {traceback.format_exc()}")

    def _on_open(self, ws):
        self.logger.info("Connected to server")

        # Resend messages from request queue
        if self.retries:
            while not self.req_queue.empty():
                message = self.req_queue.get()
             
                try:
                    self.ws_app.send(message)
                    self.logger.info(f"Sent message from queue: {message}")
             
                except Exception as e:
                    self.logger.error(f"Failed to send message from queue: {e} {traceback.format_exc()}")

        # Add to connect queue
        self.connect_queue.put(True)

    def _on_close(self, ws, code, reason):
        self.logger.warning(f"Disconnected from server: {code} {reason}")

    def _on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}")

    def _run(self):
        assert self.on_message is not None, "on_message callback must be set"

        while self.should_run:
            try:
                self.ws_app = WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error
                )

                t = Thread(target=self.ws_app.run_forever, daemon=True)
                t.start()
                t.join()
                
                if not self.should_run:
                    return

                time.sleep(5)
                
            except Exception as e:
                self.logger.error(f"Client loop error: {e} {traceback.format_exc()}")
                time.sleep(5)

    def run(self, threaded: bool = False):
        if not threaded:
            self._run()
        else:
            if self.thread is not None:
                raise RuntimeError('WsClient.run: already running')

            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()

    def start(self):
        self.run(threaded=True)

    def _is_connected(self):
        return self.ws_app and self.ws_app.sock and self.ws_app.sock.connected

    def send(self, message):
        try:
            assert self._is_connected(), "WebSocket is not connected"
            self.ws_app.send(message)

        except Exception as e:
            self.logger.error(f"Send error: {e}")

            # Add to request queue if connection is lost
            if self.retries:
                self.req_queue.put(message)
                self.logger.info("Message added to request queue for retry")

    def stop(self):
        self.should_run = False

        if self.ws_app:
            self.ws_app.close()

        if self.thread is not None:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError('WsClient.stop: failed to stop thread')

            self.thread = None
