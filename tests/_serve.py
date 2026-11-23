import sys, time
sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")
from app.core import api

srv, a = api.start_background()
print(srv.server_address[1], a.token, flush=True)
time.sleep(900)
