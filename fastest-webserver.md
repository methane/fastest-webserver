# 最速最強Webサーバーアーキテクチャ
PyFes 2012.11 発表資料です。

システムコールに焦点を当てて、 meinheld のアーキテクチャを紹介します。

アーキテクチャを説明するために Pure Python でサンプル実装を書いていますが、ちゃんと動くし、HTTPリクエストのパースを端折っているので 10000req/sec 以上出ます。
イベントドリブンのコードでフローが判りにくい場合は `python -mtrace -t --ignore-module socket webserver1.py` などのようにトレースしながら実行するといいでしょう。

## 前提
今日は、シンプルなレスポンスを返すだけの条件でをひたすら req/sec を追求する話をします。
たとえば、 nginx の lua モジュールで "hello" と返すだけとかです。
静的ファイルを配信するサーバーとかだともっと別のことも考えないといけません。

## HTTPのおさらい
詳しくは rfc2616 を見てね

Python でHTTPパースするとシステムコールじゃなくてそっちがボトルネックになっちゃうのでこの記事ではまともにHTTPパースしてません。

### HTTPリクエスト
HTTPリクエストはこんな形をしています。

```
GET / HTTP/1.1
Host: localhost

```

```
POST /post HTTP/1.1
Host: localhost
Content-Type: application/x-www-form-urlencoded
Content-Length: 7

foo=bar
```

1行目は request-line で、 ``method URI HTTP-version`` の形をしています。URIはホストを含めた絶対URIの場合と、ホストを含めない絶対パスの場合がありますが、絶対パスの方が一般的です。

2行目から空行までが request-header です。各行は ``field-name: field-value`` の形をしています。 field-name は大文字小文字を区別しません。

request-line から request-header とそれに続く空行まで、改行は CR LF になってます。Windowsでよく見る改行コードですね。

method が POST 等のときは、空行のあとに message-body がつきます。 messeage-body がどんな種類のデータなのかは `Content-Type` ヘッダで、その大きさは `Content-Length` ヘッダで指定します。 `Content-Length` を省略することもできるのですが、その時のことについては後述します。

サーバーが VirtualHost を使っているかもしれないので、request-line が絶対URIじゃない場合は "Host" ヘッダをつけてホスト名を指定してあげます。

### HTTPレスポンス
HTTPレスポンスはこんな形をしています。

```
HTTP/1.1 200 OK
Content-Type: text/plain
Content-Length: 5

Hello
```

1行目が status-line になっている以外は、HTTPリクエストとだいたい同じです。
status line は `http-version status-code reason-phrase` の形になっています。
"200 OK" とか "404 Not Found" とかいうアレですね。

## Webサーバーの基本

Web サーバーは、HTTPリクエストを受け取ってHTTPレスポンスを返すTCPサーバーです。

1. TCPポートを bind して、 listen する.
2. accpet してクライアントからの新しい接続を受け付ける
3. recv して HTTP リクエストを受け取る.
4. リクエストを処理する
5. send して HTTP レスポンスを返す.
6. close して TCP 接続を切る.

3 の recv の代わりに read, readv, recvmsg を使うことがあります。
5 の send の代わりに write, writev, writemsg を使うことがあります。

今回は req/sec を突き詰めるので、 4 は無視します。
本当は Keep-Alive にも対応しないといけないんですが、それも今回は無視します。

ここまでを Python で書くとこんなかんじです。（リクエストはパースしていません）

```python:webserver1.py
import socket


def server():
    # 1: bind & listen
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('', 8000))
    server.listen(100)
    while 1:
        # 2: accept new connection
        con, _ = server.accept()
        # 3: read request
        con.recv(32*1024)
        # 4: process request and make response
        # 5: send response
        con.sendall(b"""HTTP/1.1 200 OK\r
Content-Type: text/plain\r
Content-Length: 5\r
\r
hello""")
        # 6: close connection
        con.close()

if __name__ == '__main__':
    server()
```

## 並行処理

さっきのサンプルコードは、同時に1つのクライアントとしか通信できません。
並行して複数の接続を扱うためには幾つかの基本的な方法と、それを組み合わせた無数の方法があります。
基本的な方法は次のようなものがあります。

### accept() したあとの処理を別のスレッド or プロセスで行う
ワーカーモデルと呼びます。
ワーカースレッド/プロセスの数を動的に調整しやすいのがメリットですが、acceptしたスレッド/プロセスからワーカースレッド/プロセスに接続を引き渡す処理が必要になります。
コンテキストスイッチの負荷もかかります。

### スレッド or プロセスで accept() から close() までを行う
prefork モデルと呼びます。
accept() から close() までの処理がシンプルなままなので、うまくいけば最大の性能がでます。
ただし、スレッド／プロセス数が少ないとそれ以上の並列数を扱えず、逆に多いとコンテキストスイッチの負荷がかかります。

### epoll, select, kqueue などで多重化する
イベントドリブンモデルと呼びます。
accept できるようになったら accept して、 recv できるようになったら recv して、 send できるようになったら send します。
コンテキストスイッチが不要ですが、それぞれ専用のシステムコールの呼び出しが必要になるので、そのオーバーヘッドがかかります

## 最速のアーキテクチャ(異論は認める)

本当に hello を返すだけのサーバーなら、単純な prefork モデルでコア数だけプロセスを作るのが最強です。

```python:webserver2.py
import multiprocessing
import socket
import time


def worker(sock):
    while 1:
        con, _ = sock.accept()
        con.recv(32 * 1024)
        con.sendall(b"""HTTP/1.1 200 OK\r
Content-Type: text/plain\r
Content-Length: 5\r
\r
hello""")
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
```

gunicorn の sync worker がこのアーキテクチャのはず。

## 最強のアーキテクチャ(異論はry)

先ほどの prefork サーバーは hello というだけなら最速ですが、実用すると問題になってくるのが、リクエストの受信やレスポンスの送信に時間がかかるとそのプロセスは次のリクエストを処理できなくなって、CPUが遊んでしまうことです。

なので gunicorn の sync worker を使うときは、フロントに nginx とかを置いて静的ファイルはそっちで配信したり、リクエストやレスポンスのバッファリングをすることが推奨されてます。

でも2段構成にしたらそれだけで速度半減です。
そこで、各プロセスで epoll などを使ったイベントドリブンモデルを使って時間のかかる送受信処理も実行できるようにしてしまいます。

## 速いイベントドリブンプログラム

次のコードみたいなイベントドリブンにすると、 accept, read, write 全てにイベントドリブンのためのシステムコールがくっつくので、オーバーヘッドが増えて hello が遅くなってしまいます。

```python:webserver4.py
import socket
import select

read_waits = {}
write_waits = {}

def wait_read(con, callback):
    read_waits[con.fileno()] = callback

def wait_write(con, callback):
    write_waits[con.fileno()] = callback

def evloop():
    while 1:
        rs, ws, xs = select.select(read_waits.keys(), write_waits.keys(), [])
        for rfd in rs:
            read_waits.pop(rfd)()
        for wfd in ws:
            write_waits.pop(wfd)()

class Server(object):
    def __init__(self, con):
        self.con = con

    def start(self):
        wait_read(self.con, self.on_acceptable)

    def on_acceptable(self):
        con, _ = self.con.accept()
        con.setblocking(0)
        Client(con)
        wait_read(self.con, self.on_acceptable)


class Client(object):
    def __init__(self, con):
        self.con = con
        wait_read(con, self.on_readable)

    def on_readable(self):
        data = self.con.recv(32 * 1024)
        self.buf = b"""HTTP/1.1 200 OK\r
Content-Type: text/plain\r
Content-Length: 6\r
\r
hello
"""
        wait_write(self.con, self.on_writable)

    def on_writable(self):
        wrote = self.con.send(self.buf)
        self.buf = self.buf[wrote:]
        if self.buf:
            wait_write(self.con, self.on_writable)
        else:
            self.con.close()


def serve():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 8000))
    sock.listen(128)
    server = Server(sock)
    server.start()
    evloop()

if __name__ == '__main__':
    serve()
```

オーバーヘッドを減らすために、 wait_read, wait_write を減らせるところを探していきます。

まず、OSは accept() を呼ばなくても backlog (listen の引数に指定した数) までは自動でTCPの接続を開始しています。(SYN に対して ACK/SYN を返します) なので、アプリから accept() した時点で、実際にはTCPの接続は開始されていて、クライアントからのリクエストも受信済みかもしれません。なので、 accept() したら wait_read() せずにすぐに recv() しましょう。

read() が終わったらレスポンスの送信ですが、これも最初はソケットバッファが空のはずなのですぐに送信できます。wait_write()するのをやめましょう。


```python:webserver5.py
import socket
import select

read_waits = {}
write_waits = {}

def wait_read(con, callback):
    read_waits[con.fileno()] = callback

def wait_write(con, callback):
    write_waits[con.fileno()] = callback

def evloop():
    while 1:
        rs, ws, xs = select.select(read_waits.keys(), write_waits.keys(), [])
        for rfd in rs:
            read_waits.pop(rfd)()
        for wfd in ws:
            write_waits.pop(wfd)()

class Server(object):
    def __init__(self, con):
        self.con = con

    def start(self):
        wait_read(self.con, self.on_acceptable)

    def on_acceptable(self):
        try:
            while 1:
                con, _ = self.con.accept()
                con.setblocking(0)
                Client(con)
        except IOError:
            wait_read(self.con, self.on_acceptable)


class Client(object):
    def __init__(self, con):
        self.con = con
        self.on_readable()

    def on_readable(self):
        data = self.con.recv(32 * 1024)
        if not data:
            wait_read(self.con, self.on_readable)
            return
        self.buf = b"""HTTP/1.1 200 OK\r
Content-Type: text/plain\r
Content-Length: 6\r
\r
hello
"""
        self.on_writable()

    def on_writable(self):
        wrote = self.con.send(self.buf)
        self.buf = self.buf[wrote:]
        if self.buf:
            wait_write(self.con, self.on_writable)
        else:
            self.con.close()


def serve():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(0)
    sock.bind(('', 8000))
    sock.listen(128)
    server = Server(sock)
    server.start()
    evloop()

if __name__ == '__main__':
    serve()
```

この方式には、 prefork との相性も抜群です。

accept したあと、できる限りの処理をするまで次を accept しないので、本当に仕事が無い他のプロセスに accept を譲ることができます。

また、 thundering hard 問題も軽減できます。 thundering hard 問題とは、 prefork 方式で複数プロセスが accept() を呼び出した時、1つのクライアントが接続しただけで全部のプロセスが起き上がり、そのうち1つだけが成功するというものです。accept()が失敗したプロセスは完全に起こされ損です。安眠妨害です。1コアマシンで100プロセスとかのサーバーでこれやられたら溜まったもんじゃないです。

accept() に関しては、最近の Linux は接続が来た時に1つのプロセスのacceptだけが返るようになったので、 thundering hard 問題は完全に解決されました。しかし、 select() してから accept() する場合、この問題が再発してしまいます。

プロセス数をCPUコア数までにして、acceptの前のselectを本当に暇な時にだけ行うようにすることで、「selectしたけどacceptできない」という現象が本当にCPUがヒマなときだけしか発生しないようにできます。

これで、 accept()〜close() に必要なシステムコールが prefork に比べて setblocking(0) が増えるだけになります。ちなみに、最近の Linux だと accept4 ってシステムコールがあって accept と同時に setblocking(0) をすることもできたりします。

## 俺はユーザー空間をやめるぞ！ジョジョォ！

ここまでの話は、あくまでもユーザー空間での最速・最強です。
Webサーバーをカーネル空間で実装したら、システムコールを発行する必要無いです。

https://github.com/KLab/recaro

