bind = "0.0.0.0:8000"
# A single process owns the active door trackers. Threads handle streams and AI independently.
workers = 1
worker_class = "gthread"
threads = 12
timeout = 90
graceful_timeout = 25
limit_request_line = 4094
limit_request_fields = 40
limit_request_field_size = 4096

# The app port stays inside the Docker network. Caddy overwrites these headers.
forwarded_allow_ips = "*"
forwarder_headers = ""
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
control_socket_disable = True
