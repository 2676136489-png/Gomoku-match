"""进程级单例。

分开一个模块的唯一理由是打断循环引用：路由要 store，store 要 repo，repo 要 db，
而多个路由模块互相之间不该 import 对方。单例集中在这里，谁需要谁来拿。

这两个对象都是线程安全的（内部各自持锁），可以在 FastAPI 的线程池里共享。
"""

from __future__ import annotations

from .accounts import AccountStore
from .store import GameStore

store = GameStore()
accounts = AccountStore()
