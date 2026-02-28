import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from app.bot import run_bot

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")
        
    def log_message(self, format, *args):
        pass # Suppress HTTP logs to keep console clean

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"[*] Keep-Alive Web Server listening on port {port} for Render/Cron-job...")
    server.serve_forever()

if __name__ == "__main__":
    # Start the dummy web server in a background daemon thread
    t = threading.Thread(target=keep_alive)
    t.daemon = True
    t.start()
    
    # Start the Telegram bot polling
    run_bot()
