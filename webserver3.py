from gevent import socket
import multiprocessing
import time


def worker(sock):
    while 1:
        con, _ = sock.accept()
        con.recv(32 * 1024)
        con.sendall(b"""HTTP/1.1 200 OK\r
Content-Type: text/plain\r
Content-Length: 6\r
\r
hello
""")
        con.close()


def server():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 8000))
    sock.listen(100)

    ncpu = multiprocessing.cpu_count()
    procs = []
    for i in range(ncpu):
        proc = multiprocessing.Process(target=worker, args=(sock,))
        proc.start()
        procs.append(proc)

    while 1:
        time.sleep(0.5)

    for proc in procs:
        proc.terminate()
        proc.join(1)


if __name__ == '__main__':
    server()
