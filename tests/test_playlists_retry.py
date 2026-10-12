"""Офлайн-тест add_video: фейковий YouTube-сервіс, жодного мережевого виклику."""
import sys
sys.path.insert(0, "/home/admin1/lectures/bin")
from googleapiclient.errors import HttpError
import playlists


class Resp:
    def __init__(self, status):
        self.status, self.reason = status, "x"


def http_error(status):
    return HttpError(Resp(status), b"{}")


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSvc:
    """script: список результатів послідовних insert-ів: 'ok', 409, 404, ...
    ghost: insert повернув помилку, але відео насправді додалось."""
    def __init__(self, script, ghost=False):
        self.script, self.ghost, self.inserts, self.lists = list(script), ghost, 0, 0
        self.stored = False

    def playlistItems(self):
        svc = self

        class PI:
            def insert(self, **kw):
                def run():
                    svc.inserts += 1
                    r = svc.script.pop(0)
                    if r == "ok":
                        svc.stored = True
                        return {}
                    if svc.ghost:
                        svc.stored = True
                    raise http_error(r)
                return Req(run)

            def list(self, **kw):
                def run():
                    svc.lists += 1
                    return {"items": [{"id": "x"}] if svc.stored else []}
                return Req(run)
        return PI()


slept = []
nosleep = lambda s: slept.append(s)
fails = 0


def check(name, cond):
    global fails
    print(("OK   " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


# 1. 409 раз, потім успіх -> 2 insert-и, одна пауза 5 с
s = FakeSvc([409, "ok"]); slept.clear()
playlists.add_video(s, "PL", "V", sleep=nosleep)
check("409 → повтор → успіх (2 insert, пауза 5)", s.inserts == 2 and slept == [5] and s.stored)

# 2. успіх з першої спроби -> без пауз і без list
s = FakeSvc(["ok"]); slept.clear()
playlists.add_video(s, "PL", "V", sleep=nosleep)
check("успіх одразу (1 insert, 0 пауз, 0 list)", s.inserts == 1 and not slept and s.lists == 0)

# 3. привид: помилка, але відео вже додане -> без другого insert, без дубля
s = FakeSvc([409], ghost=True); slept.clear()
playlists.add_video(s, "PL", "V", sleep=nosleep)
check("помилка, але відео вже в плейлісті → не дублюємо (1 insert)", s.inserts == 1)

# 4. постійно 409 -> після 4 спроб піднімає помилку, паузи 5/20/60
s = FakeSvc([409, 409, 409, 409]); slept.clear()
try:
    playlists.add_video(s, "PL", "V", sleep=nosleep)
    raised = False
except HttpError:
    raised = True
check("постійний 409 → 4 спроби, паузи 5/20/60, потім виняток",
      raised and s.inserts == 4 and slept == [5, 20, 60])

# 5. нетимчасова помилка (404) -> одразу виняток, без повторів
s = FakeSvc([404]); slept.clear()
try:
    playlists.add_video(s, "PL", "V", sleep=nosleep)
    raised = False
except HttpError:
    raised = True
check("404 → одразу виняток, без повторів", raised and s.inserts == 1 and not slept)

print("ПІДСУМОК:", "усе зелене" if not fails else f"{fails} впало")
sys.exit(1 if fails else 0)
