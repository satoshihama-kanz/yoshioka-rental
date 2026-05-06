#!/bin/bash
# PythonAnywhere の Bash Console で実行するセットアップスクリプト
# このファイルをコピーしてBash Consoleに貼り付けてください

cd ~
mkdir -p yoshioka-rental
cd yoshioka-rental

# 必要ライブラリのインストール
pip install flask flask-cors gunicorn --quiet

echo "セットアップ完了!"
echo "次に Web タブで Flask アプリを設定してください"
