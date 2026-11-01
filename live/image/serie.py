# Petit client du port série des bancs VM (#26870) : python3 serie.py <socket> [délai] < commandes,
# commandes séparées par une ligne « @@ ».
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.settimeout(1)
def lire(d):
    fin = time.time() + d; out = b""
    while time.time() < fin:
        try: out += s.recv(65536)
        except socket.timeout: pass
    return out.decode(errors="replace")
s.sendall(b"\n"); print(lire(2))
for cmd in sys.stdin.read().split("\n@@\n"):
    s.sendall(cmd.encode() + b"\n"); print(lire(float(sys.argv[2]) if len(sys.argv) > 2 else 4))
