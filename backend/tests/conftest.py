import os
import sys

# backend/ をパスに追加（services, routers, main を import できるようにする）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
