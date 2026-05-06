import sys, os

# PythonAnywhere用 WSGIファイル
# ※ username の部分を自分のPythonAnywhereユーザー名に変更してください
project_home = '/home/USERNAME/yoshioka-rental'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.chdir(project_home)

from app import app as application
